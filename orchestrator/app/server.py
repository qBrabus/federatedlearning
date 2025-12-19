"""ServerApp Flower (FedAvg)."""

import os
from flwr.common import Context
from flwr.server import ServerApp, ServerConfig, ServerAppComponents
from flwr.server.strategy import FedAvg


def server_fn(context: Context) -> ServerAppComponents:
    """Construit les composants du ServerApp."""

    env_num_rounds = os.getenv("NUM_ROUNDS")
    if env_num_rounds is not None:
        num_rounds = int(env_num_rounds)
    else:
        num_rounds = context.run_config.get("num-server-rounds", 5)

    strategy = FedAvg(
        min_fit_clients=1,
        min_available_clients=1,
        min_evaluate_clients=1,
    )

    config = ServerConfig(num_rounds=num_rounds)

    return ServerAppComponents(strategy=strategy, config=config)


# Point d'entrée de l'application serveur
app = ServerApp(server_fn=server_fn)
