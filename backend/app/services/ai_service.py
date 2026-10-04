"""本地离线 AI 与云端 API 电商业务智能诊断服务。

- 本地优先（默认推荐）：连接本地 Ollama / LM Studio（端口 11434），数据 100% 留在本地，无外泄风险。
- 云端模式（需明确授权）：支持标准 OpenAI 兼容接口（如 DeepSeek、Qwen 等），带醒目安全警示。
- 业务定位：用规则引擎算准数据，用 AI 输出【异动归因 + 爆款风险 + 运营 Action Plan】。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.core import AppError
from backend.app.services import report_service

logger = logging.getLogger("ecom.ai")

SYSTEM_PROMPT = """你是一位拥有 10 年操盘经验的资深电商运营总监与数据分析专家。
你将收到某天经过本地严密计算得出的电商多平台全渠道销售数据（包括大盘指标、平台分布、异动爆款、系统预警等）。
请根据这些确定性事实，提供一份专业、犀利、结构严密的【商业经营诊断与复盘报告】。

【输出规范与原则】
1. 绝对就事论事：基于提供的数据做深度商业归因，禁止虚构无依据的数字与假大空的客套话。
2. 语言风格：专业、精炼、刀刀见血，直接指导运营实操。
3. 请严格按照以下 Markdown 格式输出：

### 一、大盘异动与业绩归因
- 分析今日 GMV、毛利、退款率的核心变化趋势。
- 深入到平台渠道与主导类目，明确说明核心业绩变化是由哪个平台或哪类商品拉动或拖累。

### 二、爆款沉浮与风险预警
- 结合涨幅榜、跌幅榜与退款榜，深入诊断重点商品。
- 识别潜在商业风险：如热销款断货降权风险、退款率异常飙升带来的客诉/质量隐患、或低 ROI 投流造成的“虚假繁荣”亏损。

### 三、明日运营行动指南 (Action Plan)
- 给出 3~5 条清晰、高优先级、可立即执行的具体运营动作（如直通车/千川限额调节、客服尺码与质量话术排查、仓储催单调拨等）。
"""


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def _fmt_pp(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}pp"


def build_diagnosis_context(report_data: Any) -> str:
    """把结构化日报数据转换为致密、高信息密度的 Prompt 上下文。"""
    ov = report_data.overview
    lines = [
        f"【报告日期】: {report_data.report_date} ({report_data.weekday})",
        "",
        "【核心大盘指标】:",
        f"- GMV: {ov.get('gmv', {}).value or 0:,.2f} 元 (环比: {_fmt_pct(ov.get('gmv', {}).dod)})",
        f"- 实际成交额: {ov.get('net_amount', {}).value or 0:,.2f} 元 (环比: {_fmt_pct(ov.get('net_amount', {}).dod)})",
        f"- 退款率: {(ov.get('refund_rate', {}).value or 0) * 100:.1f}% (环比差值: {_fmt_pp(ov.get('refund_rate', {}).dod_pp)})",
        f"- 综合毛利: {ov.get('gross_profit', {}).value or 0:,.2f} 元 (毛利率: {(ov.get('gross_margin', {}).value or 0) * 100:.1f}%)",
        f"- 经营利润: {ov.get('operating_profit', {}).value or 0:,.2f} 元",
        f"- 支付件数: {ov.get('paid_qty', {}).value or 0:,.0f} 件, 客单价: {ov.get('avg_order_value', {}).value or 0:,.2f} 元",
        f"- 访客数: {ov.get('visitors', {}).value or 0:,.0f} 人, 转化率: {(ov.get('conversion_rate', {}).value or 0) * 100:.2f}%",
        f"- 推广 ROI: {ov.get('roi', {}).value or 0:.2f}",
        "",
        "【分平台业绩分布】:",
    ]

    for p in report_data.by_platform:
        gmv_b = p.metrics.get("gmv")
        gmv_val = gmv_b.value if gmv_b else 0
        gmv_dod = _fmt_pct(gmv_b.dod) if gmv_b else "—"
        share_val = f"{(p.share or 0) * 100:.1f}%"
        ref_b = p.metrics.get("refund_rate")
        ref_val = f"{(ref_b.value or 0) * 100:.1f}%" if ref_b else "—"
        lines.append(f"- {p.name} ({p.key}): GMV {gmv_val:,.2f} 元 (占比 {share_val}, 环比 {gmv_dod}, 退款率 {ref_val})")

    lines.append("")
    lines.append("【商品异动榜单】:")
    if report_data.top.by_growth:
        lines.append("- GMV 涨幅前列:")
        for item in report_data.top.by_growth[:3]:
            lines.append(f"  * {item.get('name') or item.get('sku_code')}: GMV {item.get('value', 0):,.0f} 元, 环比 {_fmt_pct(item.get('delta'))}")
    if report_data.top.by_decline:
        lines.append("- GMV 跌幅前列:")
        for item in report_data.top.by_decline[:3]:
            lines.append(f"  * {item.get('name') or item.get('sku_code')}: GMV {item.get('value', 0):,.0f} 元, 环比 {_fmt_pct(item.get('delta'))}")
    if report_data.top.by_refund:
        lines.append("- 退款率偏高关注:")
        for item in report_data.top.by_refund[:3]:
            lines.append(f"  * {item.get('name') or item.get('sku_code')}: 退款率 {(item.get('value', 0) * 100):.1f}%, GMV {item.get('base_value', 0):,.0f} 元")

    lines.append("")
    lines.append("【系统异常预警项】:")
    if report_data.anomalies:
        for a in report_data.anomalies:
            lines.append(f"- [{a.severity.upper()}] {a.rule_name}: {a.message} (当前值: {a.actual_value}, 阈值: {a.threshold})")
    else:
        lines.append("- 今日未触发红线异常预警。")

    return "\n".join(lines)


async def run_ai_diagnosis(
    db: Session,
    date_str: str,
    provider: str = "local",
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    custom_prompt: str | None = None,
) -> dict:
    """异步调用本地或云端大模型完成业务诊断。"""
    # 0. 参数前置校验
    if provider == "cloud" and not api_key:
        raise AppError("AI_KEY_REQUIRED", "云端模式必须提供 API Key", status_code=400)

    # 1. 取日报数据（同步 pandas 聚合较重，放线程池避免阻塞事件循环）
    from starlette.concurrency import run_in_threadpool

    try:
        data = await run_in_threadpool(report_service.get_report_data, db, date_str)
    except Exception as e:
        raise AppError("AI_DIAGNOSE_NO_DATA", f"无法获取 {date_str} 的报表数据进行诊断: {e}", status_code=400)

    context_text = build_diagnosis_context(data)

    # 2. 确定终端与模型
    if provider == "local":
        target_url = (base_url or "http://localhost:11434").rstrip("/")
        if not target_url.endswith("/v1"):
            target_url = f"{target_url}/v1"
        target_model = model or "qwen2.5:7b"
        auth_header = f"Bearer {api_key or 'ollama'}"
    else:
        # 云端模式
        if not api_key:
            raise AppError("AI_KEY_REQUIRED", "云端模式必须提供 API Key", status_code=400)
        target_url = (base_url or "https://api.deepseek.com/v1").rstrip("/")
        target_model = model or "deepseek-chat"
        auth_header = f"Bearer {api_key}"

    endpoint = f"{target_url}/chat/completions"

    user_message = f"{custom_prompt}\n\n{context_text}" if custom_prompt else context_text

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": auth_header,
    }

    payload = {
        "model": target_model,
        "messages": messages,
        "temperature": 0.3,
    }

    logger.info("发起 AI 诊断请求: provider=%s, url=%s, model=%s", provider, endpoint, target_model)

    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            resp = await client.post(endpoint, json=payload, headers=headers)
        except httpx.ConnectError as e:
            if provider == "local":
                raise AppError(
                    "AI_LOCAL_NOT_FOUND",
                    f"无法连接到本地大模型服务 ({endpoint})。请确认已在本地启动 Ollama（运行 'ollama serve'）或 LM Studio，且已拉取对应模型。",
                    status_code=502,
                ) from e
            raise AppError("AI_CONNECT_ERROR", f"连接云端大模型服务失败: {e}", status_code=502) from e
        except httpx.TimeoutException as e:
            raise AppError("AI_TIMEOUT", "大模型响应超时（超过90秒），请检查模型负载或网络状态", status_code=504) from e
        except Exception as e:
            raise AppError("AI_CALL_FAILED", f"调用大模型发生网络异常: {e}", status_code=500) from e

    if resp.status_code != 200:
        err_msg = f"大模型接口返回错误 HTTP {resp.status_code}: {resp.text[:300]}"
        logger.error(err_msg)
        raise AppError("AI_API_ERROR", err_msg, status_code=resp.status_code)

    try:
        res_json = resp.json()
        diagnosis_content = res_json["choices"][0]["message"]["content"]
    except Exception as e:
        raise AppError("AI_PARSE_ERROR", f"解析大模型返回结果失败: {e}", status_code=500) from e

    return {
        "date": date_str,
        "provider": provider,
        "model": target_model,
        "diagnosis_markdown": diagnosis_content,
        "metrics_summary": {
            "gmv": data.overview.get("gmv", {}).value,
            "refund_rate": data.overview.get("refund_rate", {}).value,
            "gross_profit": data.overview.get("gross_profit", {}).value,
            "anomalies_count": len(data.anomalies),
        },
    }
