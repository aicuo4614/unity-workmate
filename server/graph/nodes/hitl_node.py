"""统一人机闸门占位：interrupt_before 挂这里，只等人，无业务。"""
from server import state

phase_name = "hitl"


async def hitl_node(_agent_state: state.AgentState):
    return {"phase": phase_name}
