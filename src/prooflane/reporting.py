"""Human-readable reports backed by run-local evidence paths."""

import html
from pathlib import Path

LABELS = {"passed": "通过", "failed": "失败", "blocked": "阻塞"}


def write_report(directory: Path, summary: dict) -> Path:
    escape = html.escape

    def artifact_link(relative: str, label: str) -> str:
        if not (directory / relative).is_file():
            return f"<span>{escape(label)}（未生成）</span>"
        return f'<a href="{escape(relative)}">{escape(label)}</a>'

    rows = []
    baseline = {case["scenario"]: case for case in summary.get("baseline", [])}
    regression = {case["scenario"]: case for case in summary.get("regression", [])}
    for case in summary.get("plan", {}).get("cases", []):
        name = case["scenario"]
        before = baseline.get(name, {"status": "blocked", "error": "未执行"})
        after = regression.get(name, {"status": "blocked", "error": "未执行"})
        evidence = " · ".join(
            f'<a href="{phase}/{escape(name)}/result.json">{label}证据</a>'
            for phase, label in [("baseline", "原版"), ("regression", "修复版")]
            if name in (baseline if phase == "baseline" else regression)
        )
        if name == "browser_checkout":
            evidence += "<br>" + " · ".join(
                f'<a href="{phase}/{name}/page.png">{label}截图</a>'
                for phase, label in [("baseline", "原版"), ("regression", "修复版")]
                if (directory / phase / name / "page.png").exists()
            )
        rows.append(
            f"<tr><td>{escape(case['requirement_id'])}</td>"
            f"<td>{escape(case.get('rationale', name))}<small>{escape(name)}</small></td>"
            f'<td class="{before["status"]}">{LABELS[before["status"]]}</td>'
            f'<td class="{after["status"]}">{LABELS[after["status"]]}</td>'
            f"<td>原版：{escape(before.get('error') or '—')}<br>"
            f"回归：{escape(after.get('error') or '—')}<br>{evidence}</td></tr>"
        )
    requirements = "".join(
        f"<li><strong>{escape(item['id'])}</strong> {escape(item['text'])} "
        f"<small>原文第 {item['line']} 行</small></li>"
        for item in summary.get("document", {}).get("requirements", [])
    )
    questions = "".join(
        f"<li>{escape(question)}</li>"
        for question in summary.get("review", summary.get("plan", {}).get("review_questions", []))
    )
    timeline = "".join(
        f"<li><strong>{escape(event['stage'])}</strong> · {escape(event['created_at'])}</li>"
        for event in summary.get("events", [])
    )
    verdict = "闭环验证完成" if summary["closed_loop"] else "闭环尚未完成"
    if summary["status"] == "completed" and summary.get("target") != "regress":
        verdict = "阶段任务完成（未执行完整闭环）"
    decisions = "".join(
        f"<li>{escape(item['action'])} / {escape(item.get('stage') or '—')}：{escape(item['reason'])}</li>"
        for item in summary.get("decisions", [])
    )
    reuse = "".join(f"<li>{escape(note)}</li>" for note in summary.get("reuse_notes", []))
    error = f'<p class="failed">{escape(summary["error"])}</p>' if summary.get("error") else ""
    counts = []
    for cases, label in [(baseline.values(), "原版"), (regression.values(), "回归")]:
        cases = list(cases)
        counts.append(
            label
            + "："
            + "，".join(
                f"{sum(case['status'] == status for case in cases)} {name}"
                for status, name in LABELS.items()
            )
        )
    page = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prooflane · 闭环测试报告</title>
<style>
body {{ margin:0; background:#f2f5f9; color:#19283e; font:16px/1.6 system-ui,sans-serif; }}
main {{ max-width:1200px; margin:36px auto; padding:0 24px 48px; }}
header,section {{ background:white; border-radius:16px; padding:26px; margin-bottom:20px; }}
h1 {{ margin:8px 0; font-size:34px; }} h2 {{ margin-top:0; }}
small {{ display:block; color:#62718a; font-size:12px; overflow-wrap:anywhere; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; border-bottom:1px solid #e2e8f0; padding:12px; vertical-align:top; }}
th {{ white-space:nowrap; }} td:nth-child(1) {{ min-width:2.5em; }} td:nth-child(3),td:nth-child(4) {{ white-space:nowrap; min-width:2em; }}
.passed {{ color:#167047; }} .failed {{ color:#b32b35; }} .blocked {{ color:#9a630d; }}
a {{ color:#2853c9; }} code {{ overflow-wrap:anywhere; }} .table {{ overflow-x:auto; }}
.badge {{ display:inline-block; background:#e9eefc; padding:5px 12px; border-radius:20px; }}
</style><main>
<header><small>PROOFLANE / REQUIREMENTS → EVIDENCE → REGRESSION</small>
<h1>{verdict}</h1><span class="badge">{escape(summary["status"])}</span>
<p>{escape("；".join(counts))}</p>
<p>任务 <code>{escape(summary["task_id"])}</code></p>
<p>本报告测试本地下单演示服务。原版含预设支付状态缺陷，修复版为仓库预先提供的代码；本次没有自动编写修复。</p>
<p>规划方式：{escape(summary.get("plan", {}).get("planner", "未完成"))}；计划生成时模型实际调用：{summary.get("plan", {}).get("model_called", False)}。</p>{error}</header>
<section><h2>调度决策</h2><p>目标：{escape(summary.get("goal", "完整闭环"))}；调度方式：{escape(summary.get("scheduler", "legacy"))}；切入点：{escape(summary.get("requested_entry") or "自动")}；终点：{escape(summary.get("target", "regress"))}。</p><ol>{decisions}</ol><ul>{reuse}</ul>{artifact_link("dispatch.json", "调度记录")}</section>
<section><h2>需求与来源</h2><p>文档 SHA-256：<code>{escape(summary.get("document", {}).get("sha256", "未导入"))}</code></p>
<ol>{requirements}</ol>{artifact_link("requirements.md", "需求快照")} · {artifact_link("plan.json", "测试计划")}</section>
<section><h2>需求审查：待确认的问题</h2><ul>{questions}</ul><p>这些是待澄清项，不是已证实的产品缺陷。</p></section>
<section><h2>原版与回归结果</h2><div class="table"><table><thead><tr><th>需求</th><th>场景</th><th>原版</th><th>回归</th><th>观察与证据</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section><h2>缺陷与修复依据</h2><p>失败结果直接来自 HTTP、页面及数据库断言。网络记录通过请求标识关联服务日志，完整证据含每次执行前后的数据库快照。</p>
<p>定位假设：回调接受了支付，但订单状态未迁移。演示修复通过一次数据库更新同时写入 paid 和支付次数，并保留幂等条件。</p>
{artifact_link("defects.json", "失败场景记录与回归状态")} · {artifact_link("source-comparison.diff", "两个支付版本的实际差异")} · {artifact_link("source-versions.json", "执行源码版本")}</section>
<section><h2>执行记录</h2><ol>{timeline}</ol><a href="summary.json">结构化结果</a> · <a href="manifest.json">证据文件摘要</a></section>
<section><h2>验证边界</h2><p>场景目录由开发者定义，文档覆盖关系不代表完整业务覆盖。浏览器禁用、工具失败、模型错误及未执行用例均不能算通过。这里的全链路限定为单个本地服务中的页面、HTTP、数据库与日志，不包含真实支付、分布式消息系统、负载测试或自动修复。</p></section>
</main></html>"""
    path = directory / "report.html"
    path.write_text(page, encoding="utf-8")
    return path
