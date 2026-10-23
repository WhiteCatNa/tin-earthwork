"""
特征线（地性线 / 断裂线）：沟边、坎边、坡顶、坡脚等折线，构网时三角形的边不得穿过。

每个顶点可以带高程（3D 折线），也可以不带（2D 折线）：
- 不带高程的顶点必须落在某个测点上，取该测点的高程；
- 带高程的顶点落在测点上时须与测点高程一致，不在测点上时作为新增的构网顶点。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

BreakVertex = Tuple[float, float, Optional[float]]


@dataclass
class Breakline:
    vertices: List[BreakVertex] = field(default_factory=list)
    name: str = ""

    def __post_init__(self):
        cleaned: List[BreakVertex] = []
        for vertex in self.vertices:
            x, y = float(vertex[0]), float(vertex[1])
            z = vertex[2] if len(vertex) > 2 else None
            cleaned.append((x, y, None if z is None else float(z)))
        self.vertices = cleaned

    def label(self, index: int) -> str:
        """提示信息里的称呼：有名称用名称，否则按序号。"""
        return f"特征线“{self.name}”" if self.name else f"第 {index + 1} 条特征线"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "vertices": [list(v) for v in self.vertices]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Breakline":
        return cls(vertices=[tuple(v) for v in data.get("vertices", [])], name=str(data.get("name", "")))
