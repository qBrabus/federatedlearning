"""ClientApp Flower."""

import logging
import os
from flwr.client import ClientApp, NumPyClient
from flwr.common import Context
import torch
from torch import nn, optim
from torch.utils.data import DataLoader, TensorDataset


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SimpleNet(nn.Module):
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


def generate_synthetic_data(num_samples: int = 512):
    x_train = torch.randn(num_samples, 1, 28, 28)
    y_train = torch.randint(0, 10, (num_samples,))
    x_val = torch.randn(num_samples // 4, 1, 28, 28)
    y_val = torch.randint(0, 10, (num_samples // 4,))
    return TensorDataset(x_train, y_train), TensorDataset(x_val, y_val)


class FlowerClient(NumPyClient):
    def __init__(self, model, trainloader, valloader, epochs, lr, device):
        self.model = model
        self.trainloader = trainloader
        self.valloader = valloader
        self.epochs = epochs
        self.lr = lr
        self.device = device
        self.model.to(self.device)

    def get_parameters(self, config):
        return [val.cpu().numpy() for val in self.model.state_dict().values()]

    def set_parameters(self, parameters):
        state_dict = self.model.state_dict()
        for key, value in zip(state_dict.keys(), parameters):
            state_dict[key] = torch.tensor(value)
        self.model.load_state_dict(state_dict)

    def fit(self, parameters, config):
        self.set_parameters(parameters)
        self.model.train()
        optimizer = optim.SGD(self.model.parameters(), lr=self.lr)
        loss_fn = nn.CrossEntropyLoss()
        for _ in range(self.epochs):
            for batch_x, batch_y in self.trainloader:
                batch_x, batch_y = batch_x.to(self.device), batch_y.to(self.device)
                optimizer.zero_grad()
                logits = self.model(batch_x)
                loss = loss_fn(logits, batch_y)
                loss.backward()
                optimizer.step()
        return self.get_parameters(config), len(self.trainloader.dataset), {}

    def evaluate(self, parameters, config):
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
        return float(loss_val / max(total, 1)), len(self.valloader.dataset), {"accuracy": correct / max(total, 1)}


def client_fn(context: Context):
    """Fonction de construction du client."""

    config = context.run_config
    epochs = int(config.get("n-local-epochs", os.getenv("N_LOCAL_EPOCHS", "1")))
    batch_size = int(config.get("batch-size", os.getenv("BATCH_SIZE", "64")))
    lr = float(config.get("learning-rate", os.getenv("LEARNING_RATE", "0.01")))

    train_ds, val_ds = generate_synthetic_data()
    trainloader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    valloader = DataLoader(val_ds, batch_size=batch_size)

    model = SimpleNet()
    cuda_available = torch.cuda.is_available()
    device = torch.device("cuda" if cuda_available else "cpu")
    logger.info("Périphérique sélectionné pour l'entraînement: %s", device)
    if not cuda_available:
        logger.warning("Aucun GPU détecté, bascule automatique sur le CPU (le GPU sera utilisé dès qu'il sera disponible).")

    return FlowerClient(model, trainloader, valloader, epochs, lr, device).to_client()


# Point d'entrée de l'application client
app = ClientApp(client_fn=client_fn)
