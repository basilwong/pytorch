# mypy: ignore-errors
import os
import sys

import pandas as pd  # type: ignore[import-untyped]


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from train_decision import AHTrainDecisionTree

from torch._inductor.autoheuristic.autoheuristic_utils import mm_operations


class AHTrainDecisionTreeMM(AHTrainDecisionTree):
    def __init__(self):
        super().__init__()

    def add_new_features(self, results):
        ops = mm_operations()
        added_categorical_features = []
        for op in ops:
            results[op.name] = results.apply(op.func, axis=1)
            if op.is_categorical:
                added_categorical_features.append(op.name)
        return (results, added_categorical_features)

    def get_default_config(self, row):
        return "extern_mm"

    def get_allowed_wrong_prediction_pct(self):
        return 1.0

    def get_test_and_val_size(self):
        return (0.01, 0.19)

    def get_grid_search_values(self):
        return {"max_depth": [5], "min_samples_leaf": [0.01], "criterion": ["entropy"]}

    def add_training_data(self, df_train, datasets):
        df_hf = datasets["mm_hf"]
        # add each dataset to the training data 3 times
        # we really want to make sure that the heuristic performs well on these datasets
        df_hf = df_hf.loc[df_hf.index.repeat(3)].reset_index(drop=True)
        df_timm = datasets["mm_timm"]
        df_timm = df_timm.loc[df_timm.index.repeat(3)].reset_index(drop=True)
        df_torchbench = datasets["mm_torchbench"]
        df_torchbench = df_torchbench.loc[df_torchbench.index.repeat(3)].reset_index(
            drop=True
        )
        df_timm_train = datasets["train_timm"]
        df_timm_train = df_timm_train.loc[df_timm_train.index.repeat(3)].reset_index(
            drop=True
        )
        df_hf_train = datasets["train_hf"]
        df_hf_train = df_hf_train.loc[df_hf_train.index.repeat(3)].reset_index(
            drop=True
        )
        df_train = datasets["train"]
        df_train = pd.concat(
            [df_train, df_hf, df_timm, df_torchbench, df_timm_train, df_hf_train],
            ignore_index=True,
        )
        return df_train

    def ranking_always_included_choices(self):
        return ["extern_mm"]

    def ranking_num_choices(self):
        return 10


if __name__ == "__main__":
    train = AHTrainDecisionTreeMM()
    train.generate_heuristic()
