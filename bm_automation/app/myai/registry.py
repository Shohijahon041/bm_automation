"""Agent metadata registry — agent visual identity and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentInfo:
    """Metadata for one agent type."""
    agent_id: str
    name: str
    role: str
    icon: str
    color: str
    description: str
    tools: list[str] = field(default_factory=list)
    order: int = 0


AGENT_REGISTRY: dict[str, AgentInfo] = {
    "master": AgentInfo(
        agent_id="master",
        name="Master",
        role="Manager",
        icon="🧠",
        color="#F0A93E",
        description="Vazifani tushunadi, rejalashtiradi va boshqaradi",
        tools=["llm"],
        order=0,
    ),
    "planner": AgentInfo(
        agent_id="planner",
        name="Planner",
        role="Analyst",
        icon="📋",
        color="#3FB6A8",
        description="Bajarilish rejasini tuzadi",
        tools=["llm"],
        order=1,
    ),
    "browser": AgentInfo(
        agent_id="browser",
        name="Browser",
        role="Computer Operator",
        icon="🌐",
        color="#6C8EEF",
        description="bm.dtransport.uz dan ma'lumot oladi",
        tools=["playwright", "dtransport", "screenshot"],
        order=2,
    ),
    "transport": AgentInfo(
        agent_id="transport",
        name="Transport",
        role="Transport Specialist",
        icon="🚌",
        color="#E8A838",
        description="Ma'lumotlarni normalizatsiya qiladi",
        tools=["normalize"],
        order=3,
    ),
    "analytics": AgentInfo(
        agent_id="analytics",
        name="Analytics",
        role="Data Analyst",
        icon="📊",
        color="#4CAF50",
        description="Hisob-kitoblarni bajaradi",
        tools=["calculate", "compare"],
        order=4,
    ),
    "reviewer": AgentInfo(
        agent_id="reviewer",
        name="Reviewer",
        role="Inspector",
        icon="🔍",
        color="#E85A4F",
        description="Natijani tekshiradi va tasdiqlaydi",
        tools=["llm", "rules", "vault"],
        order=5,
    ),
    "driver": AgentInfo(
        agent_id="driver",
        name="Driver",
        role="HR Specialist",
        icon="👤",
        color="#9C7CF4",
        description="Haydovchi ma'lumotlari",
        tools=["db"],
        order=6,
    ),
    "route": AgentInfo(
        agent_id="route",
        name="Route",
        role="Route Analyst",
        icon="🗺️",
        color="#F4845F",
        description="Yo'nalish ma'lumotlari",
        tools=["db"],
        order=7,
    ),
    "schedule": AgentInfo(
        agent_id="schedule",
        name="Schedule",
        role="Planner",
        icon="📅",
        color="#42B4E7",
        description="Jadval ma'lumotlari",
        tools=["dtransport"],
        order=8,
    ),
    "attendance": AgentInfo(
        agent_id="attendance",
        name="Attendance",
        role="Supervisor",
        icon="✅",
        color="#66BB6A",
        description="Ishga chiqish ma'lumotlari",
        tools=["calculate"],
        order=9,
    ),
    "excel": AgentInfo(
        agent_id="excel",
        name="Excel",
        role="Document Specialist",
        icon="📑",
        color="#2D7D46",
        description="Excel hisobot yaratadi",
        tools=["excel", "openpyxl"],
        order=10,
    ),
    "report": AgentInfo(
        agent_id="report",
        name="Report",
        role="Writer",
        icon="📝",
        color="#FFA726",
        description="Matnli hisobot tayyorlaydi",
        tools=["format"],
        order=11,
    ),
    "telegram": AgentInfo(
        agent_id="telegram",
        name="Telegram",
        role="Messenger",
        icon="💬",
        color="#229ED9",
        description="Telegram ga xabar yuboradi",
        tools=["telegram"],
        order=12,
    ),
    "security": AgentInfo(
        agent_id="security",
        name="Security",
        role="Guard",
        icon="🛡️",
        color="#EF5350",
        description="Ruxsatlarni tekshiradi",
        tools=["permissions"],
        order=13,
    ),
}

# Ordered list for UI display
ORDERED_AGENTS: list[AgentInfo] = sorted(
    AGENT_REGISTRY.values(), key=lambda a: a.order
)


def get_agent_info(agent_id: str) -> AgentInfo | None:
    return AGENT_REGISTRY.get(agent_id)


def all_agents() -> list[dict]:
    return [
        {
            "id": a.agent_id,
            "name": a.name,
            "role": a.role,
            "icon": a.icon,
            "color": a.color,
            "description": a.description,
            "tools": a.tools,
            "order": a.order,
        }
        for a in ORDERED_AGENTS
    ]
