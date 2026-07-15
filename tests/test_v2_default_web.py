from __future__ import annotations

from pathlib import Path

from stock_agent.web.app import create_app


def test_default_web_composes_the_production_v2_research_entry(tmp_path: Path) -> None:
    app = create_app(tmp_path)

    assert app.state.agent_service.research_entry is not None
    components = app.state.v2_components
    assert components is not None
    components.close()
