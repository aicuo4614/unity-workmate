"""模型设置 HTTP 接口；只返回脱敏状态，不回传密钥或上游原始错误。"""
import fastapi
from server.infra import model_manager

router = fastapi.APIRouter(prefix="/settings/model", tags=["settings"])


@router.get("")
async def get_config():
    return model_manager.public_config()


@router.put("")
async def save_config(req: model_manager.ModelConfig):
    try:
        return model_manager.save_config(req)
    except OSError:
        raise fastapi.HTTPException(500, "无法保存模型配置") from None
    except ValueError:
        raise fastapi.HTTPException(400, "模型配置无效") from None


@router.post("/test")
async def test_config(req: model_manager.ModelConfig):
    try:
        await model_manager.test_config(req)
    except Exception:
        # SDK 异常可能带请求头，不能把原始错误送回客户端。
        raise fastapi.HTTPException(400, "连接失败，请检查 Key、模型名称和网络") from None
    return {"ok": True}
