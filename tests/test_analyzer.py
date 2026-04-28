from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from app.services.analyzer import RepositoryAnalyzer


def test_repository_analyzer_builds_summary_and_impact(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        dedent(
            """
            def helper():
                return 1

            def calculate_payment():
                return helper()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        dedent(
            """
            from service import calculate_payment

            def run():
                return calculate_payment()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text("def test_payment():\n    assert True\n", encoding="utf-8")

    analyzer = RepositoryAnalyzer(tmp_path)
    report = analyzer.analyze()

    assert report["summary"]["total_files"] == 3
    assert report["summary"]["total_functions"] >= 3
    assert report["summary"]["total_chunks"] >= 3

    impact = analyzer.impact_analysis("calculate_payment")
    assert impact["direct_dependents"]
    assert "consumer.py" in impact["affected_files"]
    assert impact["suggested_tests"] == ["tests/test_service.py"]


def test_repository_analyzer_supports_filters_flow_search_and_diff_review(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        dedent(
            """
            def validate_login():
                return True

            def login_user():
                return validate_login()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        dedent(
            """
            from auth import login_user

            def handle_login():
                return login_user()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("function ignored() { return 1; }\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "skipped.py").write_text("def skipped():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")

    analyzer = RepositoryAnalyzer(
        tmp_path,
        include_extensions=[".py"],
        exclude_dirs=["node_modules", "build"],
    )
    report = analyzer.analyze()

    analyzed_paths = {item["path"] for item in report["files"]}
    assert analyzed_paths == {"api.py", "auth.py", "tests/test_auth.py"}
    assert report["summary"]["filters"]["include_extensions"] == [".py"]
    assert "Python" in report["summary"]["tech_stack"]

    flow = analyzer.flow_analysis("login_user")
    assert flow["steps"][0]["symbol"] == "login_user"
    assert len(flow["steps"]) >= 2

    search_results = analyzer.search("where is login validation handled")
    assert search_results
    assert any(result["file_path"] == "auth.py" for result in search_results)

    diff = analyzer.review_diff("+++ b/auth.py\n@@ -1 +1 @@\n-def validate_login():\n+def validate_login(user):\n")
    assert diff["touched_files"] == ["auth.py"]
    assert diff["risk_areas"]


def test_repository_analyzer_builds_repo_intelligence_pack_and_repo_qa(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        dedent(
            """
            from services.auth import login_user

            def analyze_repository(local_path, repo_url=None):
                return login_user(local_path)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "services" / "auth.py").write_text(
        dedent(
            """
            def validate_login(user):
                return True

            def login_user(path):
                return validate_login(path)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")

    analyzer = RepositoryAnalyzer(tmp_path, include_extensions=[".py"])
    report = analyzer.analyze()

    assert report["repo_map"]
    assert report["function_cards"]
    assert report["health"]["architecture_health"] > 0
    assert "claude" in report["context_modes"]
    assert report["diagrams"]["pipeline_mermaid"].startswith("flowchart TD")

    function_names = {card["function"] for card in report["function_cards"]}
    assert "analyze_repository" in function_names
    assert "login_user" in function_names

    ask = analyzer.ask_repo("What happens when login_user is called?")
    assert ask["mode"] == "flow"
    assert ask["evidence"]
    assert "login_user" in ask["answer"]

    pack = analyzer.export_pack("repo-intelligence-pack")
    exported_names = {item["name"] for item in pack["files"]}
    assert exported_names == {
        "repo_context.md",
        "architecture.md",
        "function_map.md",
        "improvement_plan.md",
        "architecture_diagram.mmd",
        "dependency_graph.json",
    }
