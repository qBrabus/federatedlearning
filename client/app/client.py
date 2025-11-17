"""Client Flower PyTorch de démonstration (compatible CUDA 12.4).

Le code charge un petit modèle MLP et des données synthétiques pour
valider la chaîne de bout en bout. Personnalisez le modèle, les loaders
et les métriques selon vos besoins.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import flwr as fl
import torch
from torch import nn, optim
from torch.utils.data import DataLoader, TensorDataset


@dataclass
class ClientConfig:
    """Structure de configuration alimentée par les variables d'environnement."""

    server_address: str
    client_id: str
    n_local_epochs: int
    batch_size: int
    learning_rate: float
    use_tls: bool
    ca_cert_path: str | None
    client_cert_path: str | None
    client_key_path: str | None

    @staticmethod
    def from_env() -> "ClientConfig":
        """Construit la configuration à partir des variables d'environnement."""

        return ClientConfig(
            server_address=os.getenv("SERVER_ADDRESS", "127.0.0.1:8080"),
            client_id=os.getenv("CLIENT_ID", "dgx-client"),
            n_local_epochs=int(os.getenv("N_LOCAL_EPOCHS", "1")),
            batch_size=int(os.getenv("BATCH_SIZE", "64")),
            learning_rate=float(os.getenv("LEARNING_RATE", "0.01")),
            use_tls=os.getenv("USE_TLS", "true").lower() in {"1", "true", "yes"},
            ca_cert_path=os.getenv("CA_CERT_PATH"),
            client_cert_path=os.getenv("CLIENT_CERT_PATH"),
            client_key_path=os.getenv("CLIENT_KEY_PATH"),
        )


class SimpleNet(nn.Module):
    """Petit MLP adapté à des images 28x28 (données synthétiques)."""

    def __init__(self) -> None:
        super().__init__()
        self.layer = nn.Sequential(
            nn.Flatten(),
            nn.Linear(28 * 28, 128),
            nn.ReLU(),
            nn.Linear(128, 10),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layer(x)


def generate_synthetic_data(num_samples: int = 512) -> Tuple[TensorDataset, TensorDataset]:
    """Crée des jeux d'entraînement/validation synthétiques pour un smoke test."""

    x_train = torch.randn(num_samples, 1, 28, 28)
    y_train = torch.randint(0, 10, (num_samples,))

    x_val = torch.randn(num_samples // 4, 1, 28, 28)
    y_val = torch.randint(0, 10, (num_samples // 4,))

    return TensorDataset(x_train, y_train), TensorDataset(x_val, y_val)


class FlowerClient(fl.client.NumPyClient):
    """Implémentation Flower minimale avec entraînement local PyTorch."""

    def __init__(self, model: nn.Module, trainloader: DataLoader, valloader: DataLoader, config: ClientConfig) -> None:
        self.model = model
        self.trainloader = trainloader
        self.valloader = valloader
        self.config = config
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

    def get_parameters(self, config: Dict | None = None):
        return [val.cpu().numpy() for val in self.model.state_dict().values()]

    def set_parameters(self, parameters):
        state_dict = self.model.state_dict()
        for key, value in zip(state_dict.keys(), parameters):
            state_dict[key] = torch.tensor(value)
        self.model.load_state_dict(state_dict)

    def fit(self, parameters, config):
        """Boucle d'entraînement locale pour un round Flower."""

        self.set_parameters(parameters)
        self.model.train()
        optimizer = optim.SGD(self.model.parameters(), lr=self.config.learning_rate)
        loss_fn = nn.CrossEntropyLoss()

        for _ in range(self.config.n_local_epochs):
            for batch_x, batch_y in self.trainloader:
                batch_x, batch_y = batch_x.to(self.device), batch_y.to(self.device)
                optimizer.zero_grad()
                logits = self.model(batch_x)
                loss = loss_fn(logits, batch_y)
                loss.backward()
                optimizer.step()

        return self.get_parameters(), len(self.trainloader.dataset), {}

    def evaluate(self, parameters, config):
        """Évalue le modèle local sur le jeu de validation synthétique."""

        self.set_parameters(parameters)
        self.model.eval()
        loss_fn = nn.CrossEntropyLoss()
        correct = 0
        total = 0
        loss_val = 0.0

        with torch.no_grad():
            for batch_x, batch_y in self.valloader:
                batch_x, batch_y = batch_x.to(self.device), batch_y.to(self.device)
                logits = self.model(batch_x)
                loss = loss_fn(logits, batch_y)
                loss_val += loss.item() * batch_x.size(0)
                preds = torch.argmax(logits, dim=1)
                correct += (preds == batch_y).sum().item()
                total += batch_x.size(0)

        accuracy = correct / max(total, 1)
        avg_loss = loss_val / max(total, 1)
        return float(avg_loss), len(self.valloader.dataset), {"accuracy": accuracy}


def build_tls() -> fl.common.transport.client.GrpcsSecureChannelConfig | None:
    """Construit la configuration TLS/mTLS si les fichiers sont présents."""

    ca_path = os.getenv("CA_CERT_PATH")
    client_cert = os.getenv("CLIENT_CERT_PATH")
    client_key = os.getenv("CLIENT_KEY_PATH")

    if ca_path and client_cert and client_key:
        return fl.common.transport.client.GrpcsSecureChannelConfig(
            root_certificates=Path(ca_path).read_bytes(),
            certificate_chain=Path(client_cert).read_bytes(),
            private_key=Path(client_key).read_bytes(),
        )
    return None


def main() -> None:
    """Point d'entrée principal du client Flower."""

    config = ClientConfig.from_env()
    print(f"Connecting to Flower server at {config.server_address} as {config.client_id}")

    # Charge des données synthétiques pour tester le pipeline sans dépendance externe
    train_ds, val_ds = generate_synthetic_data()
    trainloader = DataLoader(train_ds, batch_size=config.batch_size, shuffle=True)
    valloader = DataLoader(val_ds, batch_size=config.batch_size)

    model = SimpleNet()
    client = FlowerClient(model=model, trainloader=trainloader, valloader=valloader, config=config)

    tls_config = build_tls() if config.use_tls else None

    fl.client.start_numpy_client(
        server_address=config.server_address,
        client=client,
        root_certificates=tls_config.root_certificates if tls_config else None,
        certificate_chain=tls_config.certificate_chain if tls_config else None,
        private_key=tls_config.private_key if tls_config else None,
    )


if __name__ == "__main__":
    main()
