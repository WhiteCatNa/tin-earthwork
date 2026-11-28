#!/usr/bin/env python3
"""
Automated C++20 Southern CASS Workstation Goal Evaluator (100-Point Rubric).
Configures/builds cpp/build, counts compiler warnings in project sources,
runs ctest, executes tin_earthwork_benchmark --json, and prints a 5-dimension scorecard.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP_DIR = ROOT / "cpp"
BUILD_DIR = CPP_DIR / "build"
XLSX = ROOT / "test_data_1000.xlsx"
ITER_LOG = ROOT / "docs" / "cpp_iteration_log.md"


def run(cmd, cwd=ROOT):
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def count_project_warnings(build_output: str) -> int:
    # Count compiler warnings originating from cpp/src or cpp/tests (exclude third_party)
    count = 0
    for line in build_output.splitlines():
        if "warning:" in line and ("/cpp/src/" in line or "/cpp/tests/" in line or "\\cpp\\src\\" in line or "\\cpp\\tests\\" in line):
            count += 1
    return count


def compute_scores(m: dict, ctest_ok: bool, warnings: int) -> dict:
    # 1. Optimality & Accuracy (30 pts)
    s_parity = 8.0 if (m.get("parity_ok") and ctest_ok) else 0.0
    haul_ratio = m.get("haul_optimal_cases", 0) / max(1, m.get("haul_total_cases", 1))
    s_haul = round(10.0 * haul_ratio, 2)
    if m.get("has_grade_optimizer"):
        sav = float(m.get("grade_savings_pct", 0.0))
        beats_bf = bool(m.get("grade_beats_best_fit", False))
        # Up to 12 pts: 4 for feasible & <= best_fit, 8 scaled by >=20% savings vs flat balance
        s_grade = (4.0 if beats_bf else 2.0) + min(8.0, 8.0 * max(0.0, sav) / 20.0)
    else:
        s_grade = 0.0
    dim1 = round(s_parity + s_haul + s_grade, 2)

    # 2. AI Engineering Intelligence (25 pts)
    s_uncert = 7.0 if (m.get("has_uncertainty") and m.get("uncertainty_coverage_ok")) else (3.0 if m.get("has_uncertainty") else 0.0)
    rec_ratio = float(m.get("survey_rec_ratio", 0.0))
    s_rec = min(6.0, round(6.0 * rec_ratio / 1.5, 2)) if m.get("has_uncertainty") else 0.0
    if m.get("has_anomaly_detector"):
        rec = float(m.get("anomaly_recall", 0.0))
        prec = float(m.get("anomaly_precision", 0.0))
        s_anom = round(3.0 * min(1.0, rec / 0.90) + 3.0 * min(1.0, prec / 0.85), 2)
    else:
        s_anom = 0.0
    if m.get("has_ai_assistant"):
        acc = float(m.get("nl_intent_acc", 0.0))
        s_nl = round(6.0 * min(1.0, acc / 0.95), 2)
    else:
        s_nl = 0.0
    dim2 = round(s_uncert + s_rec + s_anom + s_nl, 2)

    # 3. Performance & Robustness (15 pts)
    s_perf = 0.0
    if m.get("tin_ms", 999.0) <= 3.0:
        s_perf += 3.0
    elif m.get("tin_ms", 999.0) <= 6.0:
        s_perf += 1.5
    if m.get("haul_ms", 999.0) <= 2.0:
        s_perf += 2.0
    elif m.get("haul_ms", 999.0) <= 5.0:
        s_perf += 1.0
    if m.get("has_grade_optimizer") and m.get("grade_ms", 999.0) <= 150.0:
        s_perf += 2.0
    if m.get("has_uncertainty") and m.get("uncertainty_ms", 999.0) <= 250.0:
        s_perf += 2.0
    rob_ratio = m.get("robust_passed", 0) / max(1, m.get("robust_total", 1))
    s_rob = round(6.0 * rob_ratio, 2)
    dim3 = round(s_perf + s_rob, 2)

    # 4. Southern CASS UI/UX (20 pts)
    s_ui_render = 0.0
    if m.get("ui_ok"):
        s_ui_render += 5.0
        if m.get("ui_has_ai_tab") and m.get("ui_tabs", 0) >= 7:
            s_ui_render += 3.0
        if m.get("ui_has_measure_tool"):
            s_ui_render += 2.0
    s_ui_audit = 0.0
    if float(m.get("ui_contrast", 0.0)) >= 4.5:
        s_ui_audit += 4.0
    if m.get("ui_has_tab_completion"):
        s_ui_audit += 3.0
    if m.get("has_ai_assistant") and m.get("ui_has_ai_tab"):
        s_ui_audit += 3.0
    dim4 = round(s_ui_render + s_ui_audit, 2)

    # 5. Engineering & Recursive Iteration Proof (10 pts)
    s_warn = 4.0 if warnings == 0 else max(0.0, 4.0 - 0.5 * warnings)
    s_cross = 3.0 if ((CPP_DIR / "build_windows.ps1").exists() and (CPP_DIR / "CMakeLists.txt").exists()) else 0.0
    iter_rounds = 0
    if ITER_LOG.exists():
        txt = ITER_LOG.read_text(encoding="utf-8", errors="ignore")
        iter_rounds = len(re.findall(r"^## Iteration \d+", txt, flags=re.M))
    s_log = min(3.0, float(iter_rounds))
    dim5 = round(s_warn + s_cross + s_log, 2)

    total = round(dim1 + dim2 + dim3 + dim4 + dim5, 2)
    return {
        "optimality_accuracy": dim1,
        "ai_intelligence": dim2,
        "performance_robustness": dim3,
        "ui_ux": dim4,
        "engineering_iteration": dim5,
        "total": total,
        "warnings": warnings,
        "iter_rounds_logged": iter_rounds,
    }


def compute_scores_v4(m: dict, ctest_ok: bool, warnings: int) -> dict:
    # 1. Core Accuracy & High-Scale Performance (25 pts)
    s_parity = 6.0 if (m.get("parity_ok") and ctest_ok) else 0.0
    haul_ratio = m.get("haul_optimal_cases", 0) / max(1, m.get("haul_total_cases", 1))
    s_haul = round(5.0 * haul_ratio, 2)
    tin_1k = float(m.get("tin_ms", 999.0))
    s_1k = 4.0 if tin_1k <= 1.5 else (2.5 if tin_1k <= 3.0 else 0.0)
    tin_10k = float(m.get("tin_10k_ms", 999.0))
    if tin_10k <= 25.0:
        s_10k = 10.0
    elif tin_10k <= 100.0:
        s_10k = round(10.0 * (25.0 / tin_10k), 2)
    else:
        s_10k = round(max(0.0, 2.5 * (25.0 / tin_10k)), 2)
    dim1 = round(s_parity + s_haul + s_1k + s_10k, 2)

    # 2. Constrained Multi-Objective Grade AI (25 pts)
    sav = float(m.get("grade_savings_pct", 0.0))
    s_grade = 7.0 if (m.get("has_grade_optimizer") and m.get("grade_beats_best_fit") and sav >= 20.0) else 0.0
    s_drain = 6.0 if (m.get("has_constrained_grade") and m.get("constrained_drain_ok")) else 0.0
    s_ctrl = 6.0 if (m.get("has_constrained_grade") and m.get("constrained_ctrl_ok")) else 0.0
    p_cnt = int(m.get("pareto_count", 0))
    s_pareto = 6.0 if p_cnt >= 3 else round(2.0 * max(0, p_cnt), 2)
    dim2 = round(s_grade + s_drain + s_ctrl + s_pareto, 2)

    # 3. Oriented Multi-Bench DP Terracing & Field AI (20 pts)
    s_anom = 4.0 if (m.get("has_anomaly_detector") and float(m.get("anomaly_recall", 0.0)) >= 0.90 and float(m.get("anomaly_precision", 0.0)) >= 0.85) else 0.0
    s_unc = 3.0 if (m.get("has_uncertainty") and m.get("uncertainty_coverage_ok")) else 0.0
    s_rec = 3.0 if float(m.get("survey_rec_ratio", 0.0)) >= 1.5 else 0.0
    s_bench = 0.0
    if m.get("has_multi_bench_dp"):
        vs_flat = float(m.get("multi_bench_vs_flat_pct", 0.0))
        vs_2ax = float(m.get("multi_bench_vs_2axis_pct", 0.0))
        mb_ms = float(m.get("multi_bench_ms", 999.0))
        if vs_flat >= 40.0:
            s_bench += 4.0
        if vs_2ax >= 10.0:
            s_bench += 4.0
        if mb_ms <= 25.0:
            s_bench += 2.0
    dim3 = round(s_anom + s_unc + s_rec + s_bench, 2)

    # 4. Southern CASS CAD & 3D UI/UX (20 pts)
    s_ui_v3 = 10.0 if (m.get("ui_ok") and m.get("ui_has_ai_tab") and m.get("ui_tabs", 0) >= 7 and m.get("ui_has_measure_tool") and m.get("ui_has_tab_completion") and float(m.get("ui_contrast", 0.0)) >= 4.5) else 0.0
    s_ui_v4 = 10.0 if m.get("ui_has_v4_features") else 0.0
    dim4 = round(s_ui_v3 + s_ui_v4, 2)

    # 5. Engineering & v4 Recursive Iteration Proof (10 pts)
    s_warn = 4.0 if warnings == 0 else max(0.0, 4.0 - 0.5 * warnings)
    s_cross = 3.0 if ((CPP_DIR / "build_windows.ps1").exists() and (CPP_DIR / "CMakeLists.txt").exists() and (CPP_DIR / "ITERATION_GOAL_V4.md").exists()) else 0.0
    iter_rounds = 0
    if ITER_LOG.exists():
        txt = ITER_LOG.read_text(encoding="utf-8", errors="ignore")
        v4_rounds = len(re.findall(r"v4\.\d+\.\d+", txt))
        iter_rounds = min(3, v4_rounds)
    s_log = float(iter_rounds)
    dim5 = round(s_warn + s_cross + s_log, 2)

    total = round(dim1 + dim2 + dim3 + dim4 + dim5, 2)
    return {
        "core_scale_perf": dim1,
        "constrained_multiobj_ai": dim2,
        "multi_bench_field_ai": dim3,
        "cad_3d_ux": dim4,
        "engineering_iteration": dim5,
        "total": total,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    warnings = 0
    if not args.skip_build:
        rc, out = run(["cmake", "-S", str(CPP_DIR), "-B", str(BUILD_DIR), "-DCMAKE_BUILD_TYPE=Release"])
        if rc != 0:
            print(out)
            sys.exit(1)
        rc, out = run(["cmake", "--build", str(BUILD_DIR), "--clean-first", "-j"])
        warnings = count_project_warnings(out)
        if rc != 0:
            print(out)
            sys.exit(1)

    rc_ctest, out_ctest = run(["ctest", "--test-dir", str(BUILD_DIR), "--output-on-failure"])
    ctest_ok = (rc_ctest == 0)
    if not ctest_ok:
        print(out_ctest)

    bench_bin = BUILD_DIR / "tin_earthwork_benchmark"
    rc_b, out_b = run([str(bench_bin), str(XLSX), "--json"])
    if rc_b != 0:
        print(out_b)
        sys.exit(1)
    metrics = json.loads(out_b)
    scores = compute_scores(metrics, ctest_ok, warnings)
    scores_v4 = compute_scores_v4(metrics, ctest_ok, warnings)

    print("=" * 68)
    print("TIN-CASS-AI-UI v3.0 — Regression Guard Scorecard")
    print("=" * 68)
    print(f"1. Optimality & Accuracy    : {scores['optimality_accuracy']:5.2f} / 30.00  (target >= 29.0)")
    print(f"2. AI Engineering Intel     : {scores['ai_intelligence']:5.2f} / 25.00  (target >= 24.0)")
    print(f"3. Performance & Robustness : {scores['performance_robustness']:5.2f} / 15.00  (target >= 14.0)")
    print(f"4. Southern CASS UI/UX      : {scores['ui_ux']:5.2f} / 20.00  (target >= 19.0)")
    print(f"5. Engineering & Iteration  : {scores['engineering_iteration']:5.2f} / 10.00  (target >=  9.0)")
    print("-" * 68)
    print(f"TOTAL SCORE (v3.0)          : {scores['total']:5.2f} / 100.00 (TARGET >= 95.00)")
    print("=" * 68)
    print("TIN-CASS-AI-UI v4.0 — Next-Gen North-Star Scorecard")
    print("=" * 68)
    print(f"1. Core & 10k-Pt Perf       : {scores_v4['core_scale_perf']:5.2f} / 25.00  (target >= 24.0)")
    print(f"2. Constrained Pareto AI    : {scores_v4['constrained_multiobj_ai']:5.2f} / 25.00  (target >= 24.0)")
    print(f"3. Multi-Bench DP & Field   : {scores_v4['multi_bench_field_ai']:5.2f} / 20.00  (target >= 19.0)")
    print(f"4. CAD/3D HUD & UX          : {scores_v4['cad_3d_ux']:5.2f} / 20.00  (target >= 19.0)")
    print(f"5. Engineering & Iteration  : {scores_v4['engineering_iteration']:5.2f} / 10.00  (target >=  9.0)")
    print("-" * 68)
    print(f"TOTAL SCORE (v4.0)          : {scores_v4['total']:5.2f} / 100.00 (TARGET >= 96.00)")
    print(f"Warnings (project src/tests): {warnings} | CTest: {'PASS' if ctest_ok else 'FAIL'}")
    print("Raw metrics:", json.dumps(metrics, ensure_ascii=False))
    print("=" * 68)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"metrics": metrics, "scores_v3": scores, "scores_v4": scores_v4}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
