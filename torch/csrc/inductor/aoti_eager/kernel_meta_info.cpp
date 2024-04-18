#if !defined(C10_MOBILE) && !defined(ANDROID)
#include <torch/csrc/inductor/aoti_eager/kernel_meta_info.h>
#include <iostream>

namespace torch::inductor {

TensorMetaInfo::TensorMetaInfo(const at::Tensor& src_tensor)
    : is_symbolic_(false),
      device_(src_tensor.device()),
      sizes_(src_tensor.sym_sizes().vec()),
      strides_(src_tensor.sym_strides().vec()) {
  for (const auto& size : sizes_) {
    if (size.is_symbolic()) {
      is_symbolic_ = true;
      break;
    }
  }

  if (!is_symbolic_) {
    for (const auto& stride : strides_) {
      if (stride.is_symbolic()) {
        is_symbolic_ = true;
        break;
      }
    }
  }

  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(
      !is_symbolic_,
      "Eager through torch.compile does not support symbolic shape now.");
}

TensorMetaInfo::TensorMetaInfo(
    bool is_symbolic,
    c10::ScalarType dtype,
    c10::Device device,
    std::vector<c10::SymInt> sizes,
    std::vector<c10::SymInt> strides)
    : is_symbolic_(is_symbolic),
      dtype_(dtype),
      scalar_value_((float)1.0),
      device_(device),
      sizes_(sizes),
      strides_(strides) {
  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(
      !is_symbolic_, "Not support symbolic shape now");
}

TensorMetaInfo::TensorMetaInfo(
    bool is_symbolic,
    c10::ScalarType dtype,
    c10::IValue scalar_value,
    c10::Device device,
    std::vector<c10::SymInt> sizes,
    std::vector<c10::SymInt> strides)
    : is_symbolic_(is_symbolic),
      dtype_(dtype),
      scalar_value_(scalar_value),
      device_(device),
      sizes_(sizes),
      strides_(strides) {
  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(
      !is_symbolic_, "Not support symbolic shape now");
}

bool TensorMetaInfo::operator==(const TensorMetaInfo& other) const {
  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(
      !is_symbolic_, "Not support symbolic shape now");
  return this->is_symbolic_ == other.is_symbolic_ &&
      this->dtype_ == other.dtype_ &&
      this->scalar_value_ == other.scalar_value_ &&
      this->device_.type() == other.device_.type() &&
      this->sizes_ == other.sizes_ && this->strides_ == other.strides_;
}

std::ostream& operator<<(
    std::ostream& stream,
    const TensorMetaInfo& tensor_meta_info) {
  stream << "is_symbolic_: " << tensor_meta_info.is_symbolic_ << std::endl;
  stream << "dtype_: " << tensor_meta_info.dtype_ << std::endl;
  stream << "scalar_value_: " << tensor_meta_info.scalar_value_.type()->str()
         << "(" << tensor_meta_info.scalar_value_ << ")" << std::endl;
  stream << "device_: " << tensor_meta_info.device_ << std::endl;
  stream << "sizes_: ";
  for (const auto& size : tensor_meta_info.sizes_) {
    stream << size << " ";
  }
  stream << std::endl;
  stream << "strides_: ";
  for (const auto& stride : tensor_meta_info.strides_) {
    stream << stride << " ";
  }
  stream << std::endl;
  return stream;
}

size_t TensorMetaInfoHash::operator()(
    const TensorMetaInfo& tensor_meta_info) const {
  auto hash = std::hash<bool>()(tensor_meta_info.is_symbolic_);
  hash = c10::hash_combine(
      hash, std::hash<c10::ScalarType>()(tensor_meta_info.dtype_));
  hash = c10::hash_combine(
      hash, c10::IValue::hash(tensor_meta_info.scalar_value_));
  hash = c10::hash_combine(
      hash, std::hash<c10::DeviceType>()(tensor_meta_info.device_.type()));

  for (auto& e : tensor_meta_info.sizes_) {
    if (!e.is_symbolic()) {
      hash = c10::hash_combine(hash, std::hash<int64_t>()(e.expect_int()));
    }
  }

  for (auto& e : tensor_meta_info.strides_) {
    if (!e.is_symbolic()) {
      hash = c10::hash_combine(hash, std::hash<int64_t>()(e.expect_int()));
    }
  }
  return hash;
}

size_t AOTIKernelMetaInfoHash::operator()(
    const AOTIKernelMetaInfo& aoti_kernel_meta_info) const {
  size_t hash = 0;
  for (auto& e : aoti_kernel_meta_info) {
    hash = c10::hash_combine(hash, TensorMetaInfoHash()(e));
  }
  return hash;
}

} // namespace torch::inductor
#endif
