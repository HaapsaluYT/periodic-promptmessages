import logging

import discord
from discord import app_commands
from asgiref.sync import sync_to_async
from discord.ext import commands

from django.db import IntegrityError

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.utils import checks
from ballsdex.core.utils.buttons import ConfirmChoiceView
from settings.models import load_settings, Settings, PromptMessage

from users.utils import get_user_model

log = logging.getLogger(__name__)

CATEGORY_NAMES = {
    PromptMessage.PromptType.CATCH: "Catch",
    PromptMessage.PromptType.WRONG: "Wrong",
    PromptMessage.PromptType.SPAWN: "Spawn",
    PromptMessage.PromptType.SLOW: "Slow",
}


def promptmessage_create_check():
    async def check(interaction: discord.Interaction["BallsDexBot"]) -> bool:
        user_model = get_user_model()
        try:
            dj_user = await user_model.objects.filter(discord_id=interaction.user.id).aget()
        except user_model.DoesNotExist:
            return False
        if not dj_user.is_active:
            return False
        return await dj_user.ahas_perms(["settings.add_promptmessage"])
    return app_commands.check(check)


def promptmessage_manage_check():
    async def check(interaction: discord.Interaction["BallsDexBot"]) -> bool:
        user_model = get_user_model()
        try:
            dj_user = await user_model.objects.filter(discord_id=interaction.user.id).aget()
        except user_model.DoesNotExist:
            return False
        if not dj_user.is_active:
            return False
        return await dj_user.ahas_perms(["settings.delete_promptmessage"])
    return app_commands.check(check)


class Prompts(commands.GroupCog, group_name="promptmessage", group_description="Commands for prompts"):
    """Commands for prompt messages."""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @promptmessage_create_check()
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Catch", value=1),
            app_commands.Choice(name="Wrong", value=2),
            app_commands.Choice(name="Spawn", value=3),
            app_commands.Choice(name="Slow", value=4),
        ]
    )
    async def create(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        type: int,
        message: str,
        rarity: float | None = 1.0,
    ):
        """
        Create a spawn message.

        Parameters
        ----------
        type: int
            The type of prompt message it should be. They are Catch, Wrong, Spawn, and Slow.
        message: str
            Contents of the message to be added. Supports the curly bracket substitutions.
        rarity: float
            The rarity of the spawn message. Defaults to 1.
        """
        if type > 4 or type < 1:
            await interaction.response.send_message("Invalid int value passed for the type of flag.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            vector = PromptMessage()
            vector.category = type
            vector.message = message
            vector.rarity = rarity
            if vector.settings_id is None:
                vector.settings = await sync_to_async(Settings.objects.first)()
        except IntegrityError:
            log.exception(
                f"Failed creating a prompt message because "
                f"that exact prompt message in the same category already exists.",
                exc_info=True,
                extra={"webhook": True},
            )
            await interaction.followup.send(
                f"An error occurred while creating the prompt message. Check the error in bot logs.",
                ephemeral=True,
            )
            return
        except Exception:
            log.exception(
                "Failed creating a prompt message with admin command", exc_info=True, extra={"webhook": True}
            )
            await interaction.followup.send(
                "An error occurred while creating the prompt message. Check the error in bot logs.",
                ephemeral=True,
            )
            return
        else:
            await vector.asave()
            await sync_to_async(load_settings)()
            await interaction.followup.send("A new prompt message has been created! The cache has been reloaded with the change.\n"
                        f"The message is: {vector.message}\n"
                        f"It is of category {vector.category} with rarity {vector.rarity}")
            log.info(f'{interaction.user} created a new prompt message "{vector.message}" in the category {vector.category} with rarity {vector.rarity}',
                    extra={"webhook": True})

    async def delete_message_autocomplete(
        self, interaction: discord.Interaction["BallsDexBot"], current: str
    ) -> list[app_commands.Choice[str]]:
        qs = PromptMessage.objects.all()
        if current:
            qs = qs.filter(message__icontains=current)
        results = [p async for p in qs.order_by("message")[:25]]
        return [
            app_commands.Choice(
                name=f"[{CATEGORY_NAMES.get(p.category, p.category)}] {p.message}"[:100],
                value=p.message,
            )
            for p in results
        ]

    @app_commands.command()
    @promptmessage_manage_check()
    @app_commands.autocomplete(message=delete_message_autocomplete)
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Catch", value=1),
            app_commands.Choice(name="Wrong", value=2),
            app_commands.Choice(name="Spawn", value=3),
            app_commands.Choice(name="Slow", value=4),
        ]
    )
    async def delete(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        message: str,
        type: int | None = None,
    ):
        """
        Delete a prompt message by its contents.

        Parameters
        ----------
        message: str
            The exact contents of the prompt message to delete. Start typing to search.
        type: int
            The category (Catch/Wrong/Spawn/Slow) to disambiguate, if the same message
            exists in more than one category.
        """
        qs = PromptMessage.objects.filter(message=message)
        if type is not None:
            qs = qs.filter(category=type)

        matches = [p async for p in qs]

        if not matches:
            await interaction.response.send_message(
                f"No prompt message matching that text exists"
                f"{f' in category {CATEGORY_NAMES.get(type, type)}' if type is not None else ''}.",
                ephemeral=True,
            )
            return

        if len(matches) > 1:
            options = "\n".join(
                f"- **{CATEGORY_NAMES.get(p.category, p.category)}** (rarity {p.rarity})" for p in matches
            )
            await interaction.response.send_message(
                "That message exists in multiple categories. Please re-run the command "
                f"and specify `type` to disambiguate:\n{options}",
                ephemeral=True,
            )
            return

        prompt = matches[0]

        view = ConfirmChoiceView(
            interaction,
            accept_message="Confirmed, deleting...",
            cancel_message="Request cancelled.",
        )
        await interaction.response.send_message(
            f"You are about to delete this **{CATEGORY_NAMES.get(prompt.category, prompt.category)}** "
            f"prompt message (rarity {prompt.rarity}):\n>>> {prompt.message}\n\n"
            f"Are you SURE you want to delete this spawn message?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return

        await prompt.adelete()
        await sync_to_async(load_settings)()
        await interaction.followup.send("Prompt message deleted. The cache has been reloaded.", ephemeral=True)
        log.info(
            f'{interaction.user} deleted prompt message "{prompt.message}" (category {prompt.category})',
            extra={"webhook": True},
        )