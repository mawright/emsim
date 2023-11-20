import torch
import torch.nn.functional as F
from torch import nn
from torch.distributions import MultivariateNormal


class GaussianFullRankIncidencePointPredictor(nn.Module):
    def __init__(
        self,
        backbone,
        hidden_dim=512,
        mean_parameterization="sigmoid",
        diagonal_covariance=False,
        eps=1e-6,
    ):
        super().__init__()
        self.mean_parameterization = mean_parameterization
        self.backbone = backbone
        self.diagonal_covariance = diagonal_covariance
        self.eps = 1e-6

        if diagonal_covariance:
            out_dim = 4
        else:
            out_dim = 5

        self.predictor = nn.Sequential(
            nn.Linear(self.backbone.num_features, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, out_dim),
        )

    def forward(self, x):
        if x.ndim == 3:
            x = x.unsqueeze(1)
        patch_shape = torch.as_tensor(x.shape[-2:], dtype=torch.float, device=x.device)
        patch_center_coords = patch_shape / 2
        x = self.backbone(x)
        x = self.predictor(x)

        if self.diagonal_covariance:
            mean_vector, cholesky_diagonal = torch.split(x, [2, 2], -1)
        else:
            mean_vector, cholesky_diagonal, cholesky_offdiag = torch.split(
                x, [2, 2, 1], -1
            )

        cholesky_diagonal = cholesky_diagonal.exp()
        cholesky = torch.diag_embed(cholesky_diagonal)
        if not self.diagonal_covariance:
            tril_indices = torch.tril_indices(2, 2, offset=-1, device=x.device)
            cholesky[:, tril_indices[0], tril_indices[1]] = cholesky_offdiag

        # parameterization choices
        if self.mean_parameterization == "sigmoid":
            mean_vector = F.sigmoid(mean_vector)
            # scale the mean vector to the size of the patch
            scaling_matrix = torch.diag_embed(patch_shape)
            inverse_scaling_matrix = torch.diag_embed(1 / patch_shape)
            mean_vector = torch.squeeze(scaling_matrix @ mean_vector.unsqueeze(-1), -1)
            cholesky = inverse_scaling_matrix @ cholesky

        cholesky = (
            cholesky
            + torch.eye(
                cholesky.shape[-1], dtype=cholesky.dtype, device=cholesky.device
            )
            * self.eps
        )

        return MultivariateNormal(mean_vector, scale_tril=cholesky)
