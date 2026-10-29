"""图节点的模型访问入口；配置与运行快照由独立 Manager 管理。"""
from server.infra.model_manager import get_model

__all__ = ["get_model"]
