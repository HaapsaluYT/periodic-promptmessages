import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.prompts")


async def setup(bot: "BallsDexBot"):
    log.info("Loading Prompts package...")
    from .cog import Prompts
    await bot.add_cog(Prompts(bot))
    log.info("Prompts package loaded successfully!")