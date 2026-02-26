"""测试所有 AI API 连接是否可用"""

import sys
from dotenv import load_dotenv
from openai import OpenAI
from blog_autopilot.config import (
    AISettings, SummaryQASettings,
)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
HEADERS = {"User-Agent": "MyBlogWriter/1.0"}


def test_chat(name, api_key, api_base, model, results):
    """测试 Chat Completion API"""
    try:
        client = OpenAI(api_key=api_key, base_url=api_base, default_headers=HEADERS)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
        )
        text = resp.choices[0].message.content.strip()
        results.append((name, True, f"model={model}, reply={text[:30]}"))
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))


def main():
    load_dotenv()
    ai = AISettings(_env_file=".env")
    sq = SummaryQASettings(_env_file=".env")
    results = []

    print("=" * 60)
    print("  AI API 连接测试")
    print("=" * 60)
    print()

    # 1. 质量审核 API
    if ai.model_reviewer:
        rev_key = ai.reviewer_api_key.get_secret_value() if ai.reviewer_api_key else ai.api_key.get_secret_value()
        rev_base = ai.reviewer_api_base or ai.api_base
        print("测试中: 质量审核 API...")
        test_chat("质量审核 API", rev_key, rev_base, ai.model_reviewer, results)
    else:
        results.append(("质量审核 API", True, "未配置，跳过"))

    # 2. 摘要质量评估 API
    if sq.enabled and sq.model:
        print("测试中: 摘要质量评估 API...")
        test_chat("摘要质量评估 API", sq.api_key.get_secret_value(), sq.api_base, sq.model, results)
    else:
        results.append(("摘要质量评估 API", True, "未配置/未启用，跳过"))

    # ── 输出结果 ──
    print()
    print("-" * 60)
    failed = 0
    for name, ok, detail in results:
        icon = PASS if ok else FAIL
        print(f"  {icon} {name}: {detail}")
        if not ok:
            failed += 1

    print("-" * 60)
    total = len(results)
    print(f"  共 {total} 项，通过 {total - failed}，失败 {failed}")
    print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
