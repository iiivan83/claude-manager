"""Точка входа в приложение claude_manager."""

import logging


def main():
    """Запускает приложение."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.info("Claude Manager запущен")


if __name__ == "__main__":
    main()
