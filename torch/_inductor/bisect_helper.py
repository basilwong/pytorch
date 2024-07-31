import collections
import functools
import os
import shutil
import sys
from typing import Callable, Dict, List, Optional, Tuple

from torch._inductor.runtime.cache_dir_utils import cache_dir

# Set the subdirectory name
SUBDIR_NAME = "bisect"

# Dictionary of backend -> subsystems
BACKENDS: Dict[str, List[str]] = {
    "eager": [],
    "aot_eager": [],
    "aot_eager_decomp_partition": ["decomposition"],  # TODO - add cse ?
    "inductor": [
        "post_grad_passes",
        "lowerings",
    ],  # TODO - add more - fusions, amp numeric mode ?
}

subsystem_call_counter: Dict[str, int] = collections.Counter()
call_counter_debug_info: Dict[int, str] = {}


def reset_counters() -> None:
    subsystem_call_counter.clear()
    call_counter_debug_info.clear()


@functools.lru_cache(None)
def get_env_val(env_str: str) -> Optional[str]:
    return os.environ.get(env_str, None)


class BisectionManager:
    bisection_enabled: bool = False

    @classmethod
    def get_dir(cls) -> str:
        return f"{cache_dir()}/{SUBDIR_NAME}"

    @classmethod
    def write_lines_to_file(cls, file_path: str, lines: List[str]) -> None:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as file:
            file.writelines(lines)

    @classmethod
    def read_lines_from_file(cls, file_path: str) -> List[str]:
        if os.path.exists(file_path):
            with open(file_path) as file:
                return file.readlines()
        return []

    @classmethod
    def update_run_state(
        cls, backend_name: str, subsystem_name: str, run_state: str
    ) -> None:
        file_path = os.path.join(
            cls.get_dir(), backend_name, f"{subsystem_name}_run_state.txt"
        )
        cls.write_lines_to_file(file_path, [run_state])

    @classmethod
    def update_bisect_status(cls, backend_name: str, subsystem_name: str) -> None:
        file_path = os.path.join(cls.get_dir(), "bisect_status.txt")
        lines = [f"backend={backend_name}\n", f"subsystem={subsystem_name}\n"]
        cls.write_lines_to_file(file_path, lines)

    @classmethod
    def update_bisect_range(
        cls, backend_name: str, subsystem_name: str, low: int, high: int
    ) -> None:
        file_path = os.path.join(
            cls.get_dir(), backend_name, f"{subsystem_name}_bisect_range.txt"
        )
        lines = [f"low={low}\n", f"high={high}\n"]
        cls.write_lines_to_file(file_path, lines)

    @classmethod
    def get_backend(cls) -> str:
        if val := get_env_val("TORCH_BISECT_BACKEND"):
            return val

        file_path = os.path.join(cls.get_dir(), "bisect_status.txt")
        lines = cls.read_lines_from_file(file_path)
        for line in lines:
            if line.startswith("backend="):
                return line.strip().split("=")[1]
        return ""

    @classmethod
    def get_subsystem(cls) -> str:
        if val := get_env_val("TORCH_BISECT_SUBSYSTEM"):
            return val

        file_path = os.path.join(cls.get_dir(), "bisect_status.txt")
        lines = cls.read_lines_from_file(file_path)
        for line in lines:
            if line.startswith("subsystem="):
                return line.strip().split("=")[1]
        return ""

    @classmethod
    def get_run_state(cls, backend_name: str, subsystem_name: str) -> str:
        file_path = os.path.join(
            cls.get_dir(), backend_name, f"{subsystem_name}_run_state.txt"
        )
        lines = cls.read_lines_from_file(file_path)
        if lines:
            return lines[0].strip()
        return ""

    @classmethod
    def get_bisect_range(
        cls, backend_name: str, subsystem_name: str
    ) -> Tuple[int, int]:
        file_path = os.path.join(
            cls.get_dir(), backend_name, f"{subsystem_name}_bisect_range.txt"
        )
        lines = cls.read_lines_from_file(file_path)
        low = None
        high = None
        for line in reversed(lines):
            if line.startswith("low="):
                low = int(line.strip().split("=")[1])
            elif line.startswith("high="):
                high = int(line.strip().split("=")[1])

            if low is not None and high is not None:
                break

        if low is None or high is None:
            raise RuntimeError(
                f"Trying to get bisect range when it is not set: subsystem {subsystem_name}"
            )

        return low, high

    @classmethod
    def delete_bisect_status(cls) -> None:
        if os.path.exists(cls.get_dir()):
            shutil.rmtree(cls.get_dir())
            print("Bisection status deleted.")
        else:
            print("No bisection status found.")

    @classmethod
    def get_system_counter(cls, name: str, increment: bool = True) -> int:
        global subsystem_call_counter
        curr = subsystem_call_counter[name]
        if increment:
            subsystem_call_counter[name] += 1
        return curr

    @classmethod
    def disable_subsystem(
        cls,
        backend: str,
        subsystem: str,
        debug_info: Optional[Callable[[], str]] = None,
    ) -> bool:
        if not cls.bisection_enabled:
            return False

        if cls.get_backend() != backend:
            return False

        if cls.get_subsystem() != subsystem:
            return False

        if val := get_env_val("TORCH_BISECT_MAX"):
            counter = cls.get_system_counter(subsystem, increment=True)
            return counter > int(val)

        run_state = cls.get_run_state(backend, subsystem)
        if run_state == "test_disable":
            # First run, disable completely
            return True
        elif run_state == "find_max_bounds":
            # Second run, update bisection range and return True to enable the subsystem
            cls.update_bisect_range(
                backend,
                subsystem,
                0,
                cls.get_system_counter(subsystem, increment=True),
            )
            return False
        else:
            # If the environment variable is not set, use the bisection range midpoint
            low, high = cls.get_bisect_range(backend, subsystem)
            # if high - low <= 2:
            midpoint = (low + high) // 2
            call_counter = cls.get_system_counter(subsystem)

            if (
                call_counter >= low
                and call_counter <= high
                and (low - high) <= 2
                and debug_info is not None
            ):
                call_counter_debug_info[call_counter] = debug_info()

            return call_counter > midpoint

    @classmethod
    def advance_subsystem(cls, curr_backend: str, curr_subsystem: str) -> Optional[str]:
        """
        Tries to move to the next subsystem within the current system.
        """
        print(f"Disabling {curr_subsystem} did not fix the issue.")

        current_subsystems = BACKENDS[curr_backend]
        current_subsystem_index = current_subsystems.index(curr_subsystem)

        if current_subsystem_index < len(current_subsystems) - 1:
            curr_subsystem = current_subsystems[current_subsystem_index + 1]
            cls.update_bisect_status(curr_backend, curr_subsystem)
            cls.update_run_state(curr_backend, curr_subsystem, "test_disable")
            print(f"Moving to the next subsystem: {curr_backend} - {curr_subsystem}")
            return curr_subsystem
        else:
            print(
                f"All subsystems in {curr_backend} have been checked. The issue is not in this system."
            )
            return None

    @classmethod
    def advance_backend(cls, curr_backend: str) -> Optional[str]:
        """
        Tries Move to the next backend.
        """
        current_system_index = list(BACKENDS.keys()).index(curr_backend)

        if current_system_index < len(BACKENDS) - 1:
            curr_backend = list(BACKENDS.keys())[current_system_index + 1]
            cls.update_bisect_status(curr_backend, "")
            print(f"Moving to the next system: {curr_backend}")
            return curr_backend
        else:
            return None

    @classmethod
    def perform_bisection(
        cls,
        curr_backend: str,
        curr_subsystem: str,
        fn: Callable[[], bool],
        cli_interface: bool = True,
    ) -> bool:
        """
        Perform the bisection process for the current system and subsystem. Returns True if the issue is found, False otherwise.
        """
        while True:
            run_state = cls.get_run_state(curr_backend, curr_subsystem)
            reset_counters()
            if run_state == "test_disable":
                if not fn():
                    next_subsystem = cls.advance_subsystem(curr_backend, curr_subsystem)
                    if not next_subsystem:
                        return False
                    curr_subsystem = next_subsystem
                else:
                    # breakpoint()
                    print(
                        f"Disabling {curr_subsystem} fixed the issue. Starting bisect by getting upper bound."
                    )
                    cls.update_run_state(
                        curr_backend, curr_subsystem, "find_max_bounds"
                    )
            elif run_state == "find_max_bounds":
                if fn():
                    raise RuntimeError(
                        f"Function succeeded with 'find_max_bounds' status for {curr_backend} - {curr_subsystem}."
                    )
                else:
                    _, high = cls.get_bisect_range(curr_backend, curr_subsystem)
                    print(f"Upper bound of {high} found for {curr_backend}.")
                    cls.update_run_state(curr_backend, curr_subsystem, "bisect")
            elif run_state == "bisect":
                low, high = cls.get_bisect_range(curr_backend, curr_subsystem)
                midpoint = (low + high) // 2
                print(
                    f"Bisecting {curr_backend} - {curr_subsystem} (Range: [{low}, {high}], Midpoint: {midpoint})"
                )
                if fn():
                    cls.update_bisect_range(
                        curr_backend, curr_subsystem, midpoint + 1, high
                    )
                else:
                    cls.update_bisect_range(curr_backend, curr_subsystem, low, midpoint)
                low, high = cls.get_bisect_range(curr_backend, curr_subsystem)
                if low == high:
                    print(
                        f"Binary search completed for {curr_backend} - {curr_subsystem}. The bad number is {low}. "
                        f"Debug info: {call_counter_debug_info.get(low, 'not found')}"
                    )
                    return True
            else:
                raise RuntimeError(f"Unexpected run_state {run_state}")

            if cli_interface:
                sys.exit(0)

    @classmethod
    def initialize_system(cls) -> None:
        curr_backend = next(iter(BACKENDS.keys()))
        curr_subsystem = ""
        cls.update_bisect_status(curr_backend, curr_subsystem)
        print(f"Starting bisection process with system: {curr_backend}")

    @classmethod
    def do_bisect(
        cls, fn: Callable[[], bool], cli_interface: bool = False
    ) -> Tuple[List[str], int]:
        if not cli_interface:
            bisection_enabled_orig = cls.bisection_enabled
            cls.delete_bisect_status()
            cls.bisection_enabled = True

            class DisableBisect:
                def __del__(self) -> None:
                    cls.bisection_enabled = bisection_enabled_orig
                    cls.delete_bisect_status()

            cleanup = DisableBisect()

        curr_backend = cls.get_backend()
        curr_subsystem = cls.get_subsystem()

        if not curr_backend:
            cls.initialize_system()
            curr_backend = cls.get_backend()
            curr_subsystem = cls.get_subsystem()

        while True:
            reset_counters()
            if curr_subsystem:
                result = cls.perform_bisection(
                    curr_backend, curr_subsystem, fn, cli_interface=cli_interface
                )
                if result:
                    curr_subsystem = cls.get_subsystem()
                    low, _ = cls.get_bisect_range(curr_backend, curr_subsystem)
                    return ([curr_backend, curr_subsystem], low)

                next_subsystem = cls.advance_subsystem(curr_backend, curr_subsystem)
                if not next_subsystem:
                    print(
                        f"The issue is in the {curr_backend} system, but could not identify subsystem."
                    )
                    return ([curr_backend], 0)

                curr_subsystem = next_subsystem
            else:
                if fn():
                    next_backend = cls.advance_backend(curr_backend)
                    if not next_backend:
                        print("All systems have been checked.")
                        return ([], 0)
                    curr_backend = next_backend
                else:
                    current_subsystems = BACKENDS[curr_backend]
                    if current_subsystems:
                        curr_subsystem = current_subsystems[0]
                        cls.update_bisect_status(curr_backend, curr_subsystem)
                        cls.update_run_state(
                            curr_backend, curr_subsystem, "test_disable"
                        )
                        print(
                            f"The issue is in the {curr_backend} system. Moving to the first subsystem: {curr_subsystem}"
                        )
                    else:
                        print(f"The issue is in the {curr_backend} system.")
                        return ([curr_backend], 0)

            if cli_interface:
                sys.exit(0)


def command_line_usage() -> None:
    if len(sys.argv) < 2:
        print("Usage: python bisect_update.py <start|end|good|bad>")
        sys.exit(1)

    bisection_manager = BisectionManager()
    command = sys.argv[1]

    if command == "end":
        bisection_manager.delete_bisect_status()
        sys.exit(0)

    if command == "start":
        bisection_manager.delete_bisect_status()
        bisection_manager.initialize_system()
        sys.exit(0)

    if command not in ["good", "bad"]:
        print("Invalid command. Must be 'good', 'bad', 'start', or 'end'.")
        sys.exit(1)

    def test_function() -> bool:
        return command == "good"

    if not bisection_manager.get_backend():
        raise ValueError("Must call start prior to good or bad")

    bisection_manager.do_bisect(test_function, cli_interface=True)


def get_is_bisection_enabled() -> bool:
    return (
        BisectionManager.get_subsystem() is not None
        or BisectionManager.get_backend() is not None
    )


BisectionManager.bisection_enabled = get_is_bisection_enabled()

if __name__ == "__main__":
    command_line_usage()
