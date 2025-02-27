import re

import numpy as np
import spconv.pytorch as spconv
import torch
from torch import Tensor

from .batching_utils import batch_dim_to_leading_index


def torch_sparse_to_spconv(tensor: torch.Tensor):
    """Converts a sparse torch.Tensor to an equivalent spconv SparseConvTensor

    Args:
        tensor (torch.Tensor): Sparse tensor to be converted

    Returns:
        SparseConvTensor: Converted spconv tensor
    """
    if isinstance(tensor, spconv.SparseConvTensor):
        return tensor
    assert tensor.is_sparse
    spatial_shape = tensor.shape[1:-1]
    batch_size = tensor.shape[0]
    indices_th = tensor.indices()
    features_th = tensor.values()
    if features_th.ndim == 1:
        features_th = features_th.unsqueeze(-1)
        indices_th = indices_th[:-1]
    indices_th = indices_th.permute(1, 0).contiguous().int()
    return spconv.SparseConvTensor(features_th, indices_th, spatial_shape, batch_size)


def spconv_to_torch_sparse(tensor: spconv.SparseConvTensor):
    """Converts an spconv SparseConvTensor to a sparse torch.Tensor

    Args:
        tensor (spconv.SparseConvTensor): spconv tensor to be converted

    Returns:
        torch.Tensor: Converted sparse torch.Tensor
    """
    if isinstance(tensor, Tensor) and tensor.is_sparse:
        return tensor
    assert isinstance(tensor, spconv.SparseConvTensor)
    size = [tensor.batch_size] + tensor.spatial_shape + [tensor.features.shape[-1]]
    indices = tensor.indices.transpose(0, 1)
    values = tensor.features
    return torch.sparse_coo_tensor(
        indices,
        values,
        size,
        device=tensor.features.device,
        dtype=tensor.features.dtype,
        requires_grad=tensor.features.requires_grad,
    ).coalesce()


def unpack_sparse_tensors(batch: dict[str, Tensor]):
    """
    Takes in a batch dict and converts packed sparse tensors (with separate
    indices and values tensors, and shape tuple) into sparse torch.Tensors

    Args:
        batch (dict[str, Tensor]): Input batch dict

    Returns:
        dict[str, Tensor]: Input batch dict with sparse tensors unpacked into
        sparse torch.Tensor format
    """
    prefixes_indices = [
        match[0]
        for match in [re.match(".+(?=_indices$)", key) for key in batch.keys()]
        if match is not None
    ]
    prefixes_values = [
        match[0]
        for match in [re.match(".+(?=_values$)", key) for key in batch.keys()]
        if match is not None
    ]
    prefixes_shape = [
        match[0]
        for match in [re.match(".+(?=_shape$)", key) for key in batch.keys()]
        if match is not None
    ]
    prefixes = list(set(prefixes_indices) & set(prefixes_values) & set(prefixes_shape))
    for prefix in prefixes:
        shape = batch[prefix + "_shape"]
        if isinstance(shape, Tensor):
            shape = shape.tolist()
        batch[prefix] = torch.sparse_coo_tensor(
            batch[prefix + "_indices"],
            batch[prefix + "_values"],
            shape,
            dtype=batch[prefix + "_values"].dtype,
            device=batch[prefix + "_values"].device
        ).coalesce()
        del batch[prefix + "_indices"]
        del batch[prefix + "_values"]
        del batch[prefix + "_shape"]
    return batch


def gather_from_sparse_tensor(sparse_tensor: Tensor, index_tensor: Tensor):
    """Batch selection of elements from a torch sparse tensor. Should be
    equivalent to sparse_tensor[index_tensor]. It works by flattening the sparse
    tensor's sparse dims and the index tensor to 1D (and converting n-d indices
    to raveled indices), then using index_select along the flattened sparse tensor.

    Args:
        sparse_tensor (Tensor): Sparse tensor of dimension ..., M; where ... are
        S leading sparse dimensions and M is the dense dimension
        index_tensor (Tensor): Long tensor of dimension ..., S; where ... are
        leading batch dimensions.

    Returns:
        Tensor: Tensor of dimension ..., M; where the leading dimensions are
        the same as the batch dimensions from `index_tensor`
    """
    if index_tensor.shape[-1] != sparse_tensor.sparse_dim():
        raise ValueError(
            "Expected last dim of `index_tensor` to be the same as "
            f"`sparse_tensor.sparse_dim()`, got {index_tensor.shape[-1]=} "
            f"and {sparse_tensor.sparse_dim()=}"
            )
    sparse_shape = sparse_tensor.shape[: sparse_tensor.sparse_dim()]
    dim_linear_offsets = index_tensor.new_tensor(
        [np.prod(sparse_shape[i + 1 :] + (1,)) for i in range(len(sparse_shape))]
    )

    sparse_tensor_indices_linear = (
        sparse_tensor.indices() * dim_linear_offsets.unsqueeze(-1)
    ).sum(0, keepdim=True)
    if sparse_tensor.dense_dim() > 0:
        linear_shape = (
            tuple(np.prod(sparse_shape, keepdims=True))
            + sparse_tensor.shape[-sparse_tensor.dense_dim() :]
        )
    else:
        linear_shape = tuple(np.prod(sparse_shape, keepdims=True))
    sparse_tensor_linearized = torch.sparse_coo_tensor(
        sparse_tensor_indices_linear,
        sparse_tensor.values(),
        linear_shape,
        dtype=sparse_tensor.dtype,
        device=sparse_tensor.device,
        requires_grad=sparse_tensor.requires_grad,
    )

    if index_tensor.shape[-1] != sparse_tensor.sparse_dim():
        assert index_tensor.shape[-1] == sparse_tensor.sparse_dim() - 1
        index_tensor = batch_dim_to_leading_index(index_tensor)

    index_tensor_shape = index_tensor.shape
    index_tensor_linearized = (index_tensor * dim_linear_offsets).sum(-1)
    index_tensor_linearized = index_tensor_linearized.reshape(
        -1,
    )

    selected = sparse_tensor_linearized.index_select(
        0, index_tensor_linearized
    ).to_dense()
    if sparse_tensor.dense_dim() > 0:
        selected = selected.reshape(*index_tensor_shape[:-1], selected.shape[-1])
    else:
        selected = selected.reshape(*index_tensor_shape[:-1])
    return selected


def batch_offsets_from_sparse_tensor_indices(indices_tensor: Tensor):
    """Gets the batch offsets from an index tensor where the first element of the
    first dimension is the batch index, e.g. the indices() tensor of a sparse
    torch.Tensor.

    Args:
        indices_tensor (torch.Tensor): A tensor of shape (M x nnz), where M is
        the number of dimensions of the underlying sparse tensor and nnz is the
        number of nonzero elements in the sparse tensor. Assumes the sparse
        tensor has been coalesce()d.

    Returns:
        torch.Tensor: A 1D tensor with elements corresponding the the first
        incidence of each unique element in the first position of the M axis,
        i.e., the batch offsets if the first element is the batch index.
    """
    assert not torch.is_floating_point(indices_tensor)
    batch_indices = indices_tensor[0]
    max_batch_index = batch_indices.max()
    matching_indices = batch_indices.unsqueeze(-1) == torch.arange(
        max_batch_index + 1, device=batch_indices.device, dtype=batch_indices.dtype
    )
    out = matching_indices.to(torch.uint8).argmax(0)
    return out
