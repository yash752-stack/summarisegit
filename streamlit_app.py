from __future__ import annotations

from textwrap import dedent
from typing import Any

import streamlit as st

from app.services.analyzer import RepositoryAnalyzer
from app.services.repository import materialize_repository


st.set_page_config(
    page_title="summarisegit",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def split_csv(value: str) -> list[str] | None:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def title_case(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def run_analysis(
    *,
    local_path: str,
    repo_url: str,
    branch: str,
    extensions: str,
    exclude_dirs: str,
    uploaded_file,
) -> tuple[RepositoryAnalyzer, dict[str, Any]]:
    upload_bytes = uploaded_file.getvalue() if uploaded_file is not None else None
    upload_name = uploaded_file.name if uploaded_file is not None else None

    with materialize_repository(
        local_path=local_path or None,
        repo_url=repo_url or None,
        branch=branch or None,
        upload_bytes=upload_bytes,
        upload_name=upload_name,
    ) as repo_root:
        analyzer = RepositoryAnalyzer(
            repo_root,
            branch=branch or None,
            include_extensions=split_csv(extensions),
            exclude_dirs=split_csv(exclude_dirs),
        )
        report = analyzer.analyze()
    return analyzer, report


if "report" not in st.session_state:
    st.session_state.report = None
if "analyzer" not in st.session_state:
    st.session_state.analyzer = None
if "last_impact" not in st.session_state:
    st.session_state.last_impact = None
if "last_flow" not in st.session_state:
    st.session_state.last_flow = None
if "last_ask" not in st.session_state:
    st.session_state.last_ask = None
if "last_pr_review" not in st.session_state:
    st.session_state.last_pr_review = None


st.title("summarisegit")
st.caption(
    "Repo-to-Claude context compiler and code intelligence workstation for unfamiliar repositories. "
    "No paid API is used in the current build."
)

with st.sidebar:
    st.header("Analyze a repo")
    local_path = st.text_input("Local path", placeholder="/path/to/repo")
    repo_url = st.text_input("GitHub repo URL", placeholder="https://github.com/org/repo")
    branch = st.text_input("Branch", value="main")
    extensions = st.text_input("Extensions", value=".py,.js,.ts,.tsx")
    exclude_dirs = st.text_input("Exclude dirs", value="node_modules,dist,build,.git,.venv")
    uploaded_file = st.file_uploader("Upload zip", type=["zip"])

    analyze_clicked = st.button("Analyze repository", use_container_width=True)
    st.markdown("### Current engine")
    st.info(
        "Local AST parsing + JS structural parsing + TF-IDF retrieval + graph traversal. "
        "No OpenAI, Anthropic, or Groq API required."
    )

if analyze_clicked:
    try:
        with st.spinner("Building Repo Intelligence Pack..."):
            analyzer, report = run_analysis(
                local_path=local_path,
                repo_url=repo_url,
                branch=branch,
                extensions=extensions,
                exclude_dirs=exclude_dirs,
                uploaded_file=uploaded_file,
            )
        st.session_state.analyzer = analyzer
        st.session_state.report = report
        st.session_state.last_impact = None
        st.session_state.last_flow = None
        st.session_state.last_ask = None
        st.session_state.last_pr_review = None
        st.success(f"Analyzed {report['summary']['repo_name']}")
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))

report = st.session_state.report
analyzer = st.session_state.analyzer

if report is None or analyzer is None:
    st.markdown(
        dedent(
            """
            ### What this app gives you
            - Repo Intelligence Pack exports for Claude, interviews, PR review, and architecture handoff
            - Function-level cards with inputs, outputs, calls, and risk labels
            - Impact analysis and suggested test targets
            - Auto-generated repo map, architecture summary, and improvement plan
            - Ask-repo mode for structural questions about unfamiliar codebases
            """
        )
    )
    st.stop()

summary = report["summary"]
health = report["health"]

metric_cols = st.columns(4)
metric_cols[0].metric("Files", summary["total_files"])
metric_cols[1].metric("Functions", summary["total_functions"])
metric_cols[2].metric("Architecture Health", f"{health['architecture_health']}/100")
metric_cols[3].metric("Risk Hotspots", health["risk_hotspots"])

health_cols = st.columns(4)
health_cols[0].metric("Maintainability", health["maintainability"])
health_cols[1].metric("Missing Tests", health["missing_tests"])
health_cols[2].metric("Search Backend", report["metrics"]["search_backend"])
health_cols[3].metric("Chunks", report["metrics"]["chunk_count"])

with st.expander("Repo map", expanded=False):
    for category in report["repo_map"]:
        with st.container(border=True):
            st.subheader(category["name"])
            for item in category["files"]:
                st.markdown(f"**{item['path']}**")
                st.caption(" · ".join(item["responsibilities"]))

summary_tab, architecture_tab, function_tab, impact_tab, improve_tab, ask_tab, export_tab = st.tabs(
    [
        "Summary",
        "Architecture",
        "Function Map",
        "Impact",
        "Improvements",
        "Ask Repo",
        "Export",
    ]
)

with summary_tab:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Tech stack")
        st.write(summary["tech_stack"] or ["Unknown"])
        st.subheader("Folder summaries")
        for item in report["folder_summaries"]:
            with st.container(border=True):
                st.markdown(f"**{item['folder']}**")
                st.write(item["summary"])
    with right:
        st.subheader("Biggest files")
        st.dataframe(summary["biggest_files"], use_container_width=True)
        st.subheader("Most-used functions")
        st.dataframe(summary["most_used_functions"], use_container_width=True)

with architecture_tab:
    audience_label = st.segmented_control(
        "Explanation mode",
        options=["Engineer", "Newcomer"],
        default="Engineer",
        key="architecture_audience",
    )
    audience = "newcomer" if audience_label == "Newcomer" else "engineer"
    architecture = analyzer.explain_architecture(audience=audience)

    st.subheader("Architecture narrative")
    st.markdown(f"**{architecture['headline']}**")
    st.write(architecture["narrative"])

    st.subheader("Central files")
    st.dataframe(architecture["central_files"], use_container_width=True)

    diag_left, diag_right = st.columns(2)
    with diag_left:
        st.subheader("Block diagram")
        st.code(report["diagrams"]["block_diagram"])
    with diag_right:
        st.subheader("Mermaid export")
        st.code(report["diagrams"]["pipeline_mermaid"], language="mermaid")

    st.subheader("File dependency edges")
    st.dataframe(report["file_graph"]["links"][:150], use_container_width=True)

with function_tab:
    cards = report["function_cards"]
    options = {f"{card['function']} — {card['file_path']}": card for card in cards}
    selected_label = st.selectbox("Select a function", list(options.keys()))
    selected_card = options[selected_label]
    st.markdown(f"### {selected_card['function']}")
    st.write(selected_card["purpose"])
    details_left, details_right = st.columns(2)
    with details_left:
        st.write({
            "file": selected_card["file_path"],
            "inputs": selected_card["inputs"],
            "output": selected_card["output"],
            "calls": selected_card["calls"],
        })
    with details_right:
        st.write({
            "risk_label": selected_card["risk_label"],
            "risk_score": selected_card["risk_score"],
            "why_risky": selected_card["why_risky"],
            "direct_dependents": selected_card["direct_dependents"],
        })
    symbol_explanation = analyzer.explain_symbol(selected_card["function"])
    st.subheader("Snippet")
    st.code(symbol_explanation["snippet"])

with impact_tab:
    impact_target = st.text_input("Function or file to analyze", key="impact_target")
    impact_clicked = st.button("Analyze impact")
    if impact_clicked and impact_target:
        try:
            st.session_state.last_impact = analyzer.impact_analysis(impact_target)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    if st.session_state.last_impact:
        impact = st.session_state.last_impact
        st.json(
            {
                "target": impact["target"],
                "risk_score": impact["risk_score"],
                "direct_dependents": impact["direct_dependents"],
                "indirect_dependents": impact["indirect_dependents"],
                "affected_files": impact["affected_files"],
                "suggested_tests": impact["suggested_tests"],
            }
        )
        st.write(impact["explanation"])

    flow_target = st.text_input("Trace execution from", key="flow_target")
    flow_clicked = st.button("Trace flow")
    if flow_clicked and flow_target:
        try:
            st.session_state.last_flow = analyzer.flow_analysis(flow_target)
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    if st.session_state.last_flow:
        flow = st.session_state.last_flow
        st.markdown(f"**{flow['target']}**")
        st.write(flow["narrative"])
        st.code(flow["mermaid"], language="mermaid")
        st.dataframe(flow["steps"], use_container_width=True)

with improve_tab:
    st.subheader("Architecture suggestions")
    st.dataframe(report["improvements"]["architecture_suggestions"], use_container_width=True)
    st.subheader("Suggested structure")
    st.code(report["improvements"]["suggested_structure"])

    for name, items in report["improvements"]["sections"].items():
        if not items:
            continue
        with st.expander(title_case(name)):
            st.dataframe(items[:15], use_container_width=True)

with ask_tab:
    question = st.text_input("Ask about the repo", key="ask_question", placeholder="What happens when /api/analyze is called?")
    ask_clicked = st.button("Ask repo")
    if ask_clicked and question:
        st.session_state.last_ask = analyzer.ask_repo(question)

    if st.session_state.last_ask:
        answer = st.session_state.last_ask
        st.markdown(f"### {title_case(answer['mode'])}")
        st.write(answer["answer"])
        if answer.get("mermaid"):
            st.code(answer["mermaid"], language="mermaid")
        st.write("Evidence", answer.get("evidence", []))
        st.write("Next steps", answer.get("next_steps", []))

with export_tab:
    st.subheader("Context compression modes")
    mode_name = st.selectbox("Preview mode", list(report["context_modes"].keys()))
    st.code(report["context_modes"][mode_name])

    st.subheader("Repo Intelligence Pack downloads")
    pack = analyzer.export_pack("repo-intelligence-pack")
    for file in pack["files"]:
        st.download_button(
            label=f"Download {file['name']}",
            data=file["content"],
            file_name=file["name"],
            mime=file.get("content_type", "text/plain"),
            use_container_width=True,
        )

    st.subheader("Interview export")
    interview_pack = analyzer.export_pack("interview")
    for file in interview_pack["files"]:
        st.download_button(
            label=f"Download {file['name']}",
            data=file["content"],
            file_name=file["name"],
            mime=file.get("content_type", "text/plain"),
            use_container_width=True,
            key=f"interview-{file['name']}",
        )
