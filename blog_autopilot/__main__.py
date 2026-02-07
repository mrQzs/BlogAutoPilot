"""python -m blog_autopilot 入口"""

import sys

from blog_autopilot.config import get_settings, setup_logging
from blog_autopilot.pipeline import Pipeline


def main() -> None:
    logger = setup_logging()

    if "--test" in sys.argv:
        settings = get_settings()
        Pipeline(settings).run_test()
        return

    once_mode = "--once" in sys.argv

    try:
        settings = get_settings()
    except Exception as e:
        logger.error(f"配置加载失败: {e}")
        sys.exit(1)

    Pipeline(settings).run(once=once_mode)


if __name__ == "__main__":
    main()
