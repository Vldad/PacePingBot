import os
import asyncpg
from datetime import datetime
import re
import discord
from discord.ext import commands
from discord import app_commands
import typing

class PacePingBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pg_con = None
        self.add_commands()
        self.paces_list = []

    async def setup_hook(self):
        if not self.pg_con:
            print("Setting up db..")
            self.pg_con = await asyncpg.create_pool(database=os.getenv('DB_PP'), user=os.getenv('DB_PP_USER'), password=os.getenv('DB_PP_USER_PWD'))
    
    # method called when the bot has the status ready.
    # we force the sync of the tree of commands
    # then we retrieve all the current paces to help the autocompletion
    async def on_ready(self):
        try:
            sc = await self.tree.sync()
            print(f"synced {len(sc)} commands")
        except Exception as e:
            print(e)
        
        await self.get_all_paces()
        
        print(f"{__name__} is up and ready!")

    def add_commands(self):
        # this command allows the user to create a new pace to subscribe to
        @self.tree.command(name="pace_add", description="Allows an authorized user to create a new pace to subscribe to.")
        @app_commands.describe(pace_label="The name of the pace you want to add")
        async def add_pace(interaction: discord.Interaction, pace_label: str):
            # first, check if the user has the right to do so.
            # to do so, the bot checks the roles. The user must have at least one of the following roles "owner", "modo" or "PacePingBoss"
            if not await self.check_role_access("PacePingBoss", interaction.user.roles):
                await interaction.response.send_message(f"{interaction.user.name} is not permitted to add paces", ephemeral=True)
                return
            
            con = await self.pg_con.acquire()
            # then, we check if the pace that we want to add has a valid format
            if await self.check_pace_label(pace_label):
                # then, we check if the pace doesn't already exist
                if not await self.pace_exists(pace_label):
                    # if not, then let's create it
                    await self.pg_con.execute(f"INSERT INTO public.dim_paces(\"PaceLabel\") VALUES ('{pace_label}')")
                    await interaction.response.send_message(f"The pace **{pace_label}** has been created. Everybody can now subscribe to it.", ephemeral=True)
                    channel = await interaction.guild.fetch_channel(int(os.getenv('PING_CHANNEL_ID')))
                    await channel.send(f"{interaction.user.display_name} just created the pace **{pace_label}**. Everybody can now subscribe to it :)")
                    await self.get_all_paces()
                else:
                    await interaction.response.send_message(f"The pace {pace_label} already exists !", ephemeral=True)
            else:
                await interaction.response.send_message(f"Your pace name does not meet the requirements. Only numbers, lowercases, uppercases and underscores(_) are authorized.", ephemeral=True)
            # log the action
            await self.log(interaction.user.id, datetime.now() , f'pace_add {pace_label}')
            await self.pg_con.release(con)
        
        # this command allows the user to display the list of available paces to subscribe to
        @self.tree.command(name="pace_list", description="Displays all the paces available to subscription.")
        async def pace_list(interaction: discord.Interaction):
            con = await self.pg_con.acquire()
            # returns the list of paces and their number of followers/subscribers
            resultset = await self.pg_con.fetch("SELECT \"PaceLabel\", \"NbFollowers\" FROM public.dim_paces")
            message = ''
            # check the number of available paces
            if len(resultset) > 0:
                message = 'List of paces you can subscribe to :'
                for r in resultset:
                    message += '\n\t=> ' + str(r[0]) + '\t subscribers : ' + str(r[1])
            else:
                message = 'There is no active pace. Please create it first with the command "/pace_add [pace_label]"'
            await interaction.response.send_message(message, ephemeral=True)
            await self.pg_con.release(con)

        # this command allows the user to subscribe to a given pace
        @self.tree.command(name="pace_sub", description="Allows you to subscribe to a pace")
        @app_commands.describe(pace_label="The name of the pace you want to subscribe to")
        async def pace_sub(interaction: discord.Interaction, pace_label: str):
            con = await self.pg_con.acquire()
            # first, we check if the pace that we want to add has a valid format
            if await self.check_pace_label(pace_label):
                # then check if the given pace exists
                if await self.pace_exists(pace_label):
                    # then check if the user isn't already subscribed to the given pace
                    if not await self.is_user_subsribed(interaction.user.id, pace_label):
                        # subscribe the user to the given pace
                        await self.pg_con.execute(f"INSERT INTO public.fait_paces_users(\"PaceId\",\"DiscordId\") SELECT \"PaceId\", '{interaction.user.id}' FROM public.dim_paces WHERE \"PaceLabel\"='{pace_label}'")
                        # increments the total number of subscribers to the given pace
                        await self.pg_con.execute(f"UPDATE public.dim_paces set \"NbFollowers\"=\"NbFollowers\"+1 WHERE \"PaceLabel\"='{pace_label}'")
                        await interaction.response.send_message(f'You have been successfully subscribed to the pace **{pace_label}**', ephemeral=True)
                    else:
                        await interaction.response.send_message(f'You are already subscribed to **{pace_label}** !', ephemeral=True)
                else:
                    await interaction.response.send_message(f'The pace **{pace_label}** doesn''t exist. Please create it first with the command **/pace_add [pace_name]**', ephemeral=True)
            else:
                await interaction.response.send_message(f"Your pace name does not meet the requirements. Only numbers, lowercases, uppercases and underscores(_) are authorized.", ephemeral=True)
            await self.pg_con.release(con)
        
        # this method handles the autocompletion for the command pace_sub
        @pace_sub.autocomplete("pace_label")
        async def pace_sub_autocompletion(interaction: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
            data = []
            for pace in self.paces_list:
                if current.lower() in pace.lower():
                    data.append(app_commands.Choice(name=pace, value=pace))
            return data
        
        # this command allows the user to display the list of the paces he's subscribed to
        @self.tree.command(name="pace_my", description="Allows you to display the paces you're subscribed to")
        async def pace_my(interaction: discord.Interaction):
            con = await self.pg_con.acquire()
            resultset = await self.pg_con.fetch(f"SELECT dp.\"PaceLabel\", dp.\"NbFollowers\" FROM public.dim_paces dp INNER JOIN public.fait_paces_users fpu ON fpu.\"PaceId\"=dp.\"PaceId\" WHERE fpu.\"DiscordId\"='{interaction.user.id}'")
            # check the number of paces you are subscribed to
            if len(resultset) > 0:
                message = 'List of paces you are subscribed to :'
                for r in resultset:
                    message += '\n\t=> ' + str(r[0]) + '\t subscribers : ' + str(r[1])
            else:
                message = "You are not subscribed to any pace."
            await interaction.response.send_message(message, ephemeral=True)
            await self.pg_con.release(con)
        
        # this command allows the user to unsubscribe from a pace he's subscribed to
        @self.tree.command(name="pace_unsub", description="Allows you to unsubscribe from a pace you're no longer interested in")
        @app_commands.describe(pace_label="The name of the pace you want to unsubscribe from")
        async def pace_unsub(interaction: discord.Interaction, pace_label: str):
            con = await self.pg_con.acquire()
            # first, we check if the pace that we want to add has a valid format
            if await self.check_pace_label(pace_label):
                # then check if the given pace exists
                if await self.pace_exists(pace_label):
                    # then check if the user is indeed subscribed to it
                    if await self.is_user_subsribed(interaction.user.id, pace_label):
                        # unsubscribe the user to said pace
                        await self.pg_con.execute(f"DELETE FROM fait_paces_users WHERE \"PaceId\" IN (SELECT \"PaceId\" FROM dim_paces WHERE \"PaceLabel\"='{pace_label}') AND \"DiscordId\"='{interaction.user.id}'")
                        # decrements the total number of subscribers to the given pace
                        await self.pg_con.execute(f"UPDATE public.dim_paces set \"NbFollowers\"=\"NbFollowers\"-1 WHERE \"PaceLabel\"='{pace_label}'")
                        await interaction.response.send_message(f"You have been successfully unsubscribed from the pace **{pace_label}**", ephemeral=True)
                    else:
                        await interaction.response.send_message(f'You are not subscribed to **{pace_label}**', ephemeral=True)
                else:
                    await interaction.response.send_message(f'The pace **{pace_label}** doesn''t exist. You can check your subscriptions with the command **/pace_my**', ephemeral=True)
            else:
                await interaction.response.send_message(f"Your pace name does not meet the requirements. Only numbers, lowercases, uppercases and underscores(_) are authorized.", ephemeral=True)
            await self.pg_con.release(con)

        # this method handles the autocompletion for the command pace_unsub
        @pace_unsub.autocomplete("pace_label")
        async def pace_unsub_autocompletion(interaction: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
            data = []
            for pace in self.paces_list:
                if current.lower() in pace.lower():
                    data.append(app_commands.Choice(name=pace, value=pace))
            return data
        
        # this command allows the bot to ping users subscribed to a given pace
        @self.tree.command(name="pace_ping", description="Allows an authorized user to ping everyone subscribed to a given pace")
        @app_commands.describe(pace_label="The name of the pace whose subscribers you want to ping")
        @app_commands.describe(ping_message="The message to describe the kind of pace (time at chapter exit, checkpoint of the golden, etc...)")
        @app_commands.describe(live_link="The url to the live we can see the current pace (twitch, youtube, etc...)")
        async def pace_ping(interaction: discord.Interaction, pace_label: str, ping_message: str, live_link: str):
            con = await self.pg_con.acquire()
            if ping_message == "":
                ping_message = "Live pace !"
            # first, check if the user has the right to do so.
            # to do so, the bot checks the roles. The user must have at least one of the following roles "owner", "modo" or "PacePingBoss"
            if not await self.check_role_access("PacePingBoss", interaction.user.roles):
                await interaction.response.send_message(f"You are not permitted to ping paces", ephemeral=True)
                return
            
            # then, we check if the pace that we want to add has a valid format
            if await self.check_pace_label(pace_label):
                # then check if the given pace exists
                if await self.pace_exists(pace_label):
                    resultset = await self.pg_con.fetch(f"SELECT fpu.\"DiscordId\" FROM dim_paces dp INNER JOIN fait_paces_users fpu ON fpu.\"PaceId\"=dp.\"PaceId\" WHERE dp.\"PaceLabel\"='{pace_label}'")
                    message = f'Pinging all the users subsribed to the pace **{pace_label}** : {ping_message} => {live_link}\n'
                    for r in resultset:
                        message += f'<@{r[0]}> '
                    channel = await interaction.guild.fetch_channel(int(os.getenv('PING_CHANNEL_ID')))
                    await channel.send(message)
                    await interaction.response.send_message(f'Subscribers to the pace **{pace_label}** have been pinged !', ephemeral=True)
                else:
                    await interaction.response.send_message(f'The pace **{pace_label}** doesn''t exist. Check the names first with the command **/pace_my**.', ephemeral=True)
            else:
                await interaction.response.send_message(f"Your pace name does not meet the requirements. Only numbers, lowercases, uppercases and underscores(_) are authorized.", ephemeral=True)
            # log the action
            await self.log(interaction.user.id, datetime.now(), f'pace_ping {pace_label}')
            await self.pg_con.release(con)
        
        # this method handles the autocompletion for the command pace_ping
        @pace_ping.autocomplete("pace_label")
        async def pace_ping_autocompletion(interaction: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
            data = []
            for pace in self.paces_list:
                if current.lower() in pace.lower():
                    data.append(app_commands.Choice(name=pace, value=pace))
            return data
        
        @self.tree.command(name="pace_remove", description="Allows an authorized user to remove a pace and all its subscriptions")
        @app_commands.describe(pace_label="The name of the pace you want to remove")
        async def pace_remove(interaction: discord.Interaction, pace_label: str):
            con = await self.pg_con.acquire()
            # first, check if the user has the right to do so.
            # to do so, the bot checks the roles. The user must have at least one of the following roles "owner", "modo" or "PacePingBoss"
            if not await self.check_role_access("PacePingBoss", interaction.user.roles):
                await interaction.response.send_message(f"You are not permitted to remove paces", ephemeral=True)
                return
            
            # then, we check if the pace that we want to add has a valid format
            if await self.check_pace_label(pace_label):
            # then check if the given pace exists
                if await self.pace_exists(pace_label):
                    # if so, then remove all the subscriptions first
                    await self.pg_con.execute(f"DELETE FROM fait_paces_users WHERE \"PaceId\" IN (SELECT \"PaceId\" FROM dim_paces WHERE \"PaceLabel\"='{pace_label}')")
                    # then delete the pace itself
                    await self.pg_con.execute(f"DELETE FROM dim_paces WHERE \"PaceLabel\"='{pace_label}'")
                    await interaction.response.send_message(f'The pace **{pace_label}** has been removed as well as all its subscriptions.', ephemeral=True)
                    channel = await interaction.guild.fetch_channel(int(os.getenv('PING_CHANNEL_ID')))
                    await self.get_all_paces()
                    await channel.send(f"{interaction.user.display_name} just removed the pace **{pace_label}** as well as all its subscriptions.")
                else:
                    await interaction.response.send_message(f'The pace **{pace_label}** doesn''t exist. You can check the paces with the command **/pace_list**', ephemeral=True)
            else:
                await interaction.response.send_message(f"Your pace name does not meet the requirements. Only numbers, lowercases, uppercases and underscores(_) are authorized.", ephemeral=True)

            # log the action
            await self.log(interaction.user.id, datetime.now(), f'pace_remove {pace_label}')
            await self.pg_con.release(con)

        # this method handles the autocompletion for the command pace_remove
        @pace_remove.autocomplete("pace_label")
        async def pace_remove_autocompletion(interaction: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
            data = []
            for pace in self.paces_list:
                if current.lower() in pace.lower():
                    data.append(app_commands.Choice(name=pace, value=pace))
            return data

    # this method checks if the pace that one wants to add doesn't already exists
    async def pace_exists(self, pace_label):
        resultset = await self.pg_con.fetchrow(f"SELECT count(1) FROM public.dim_paces WHERE \"PaceLabel\"='{pace_label}'")
        return False if resultset[0] == 0 else True
    
    # this method checks if the role of the user is in the list of authorized roles
    async def check_role_access(self, user_role, author_roles):
        return True if 'owner' in [ar.name.lower() for ar in author_roles] or 'modo' in [ar.name.lower() for ar in author_roles] or user_role.lower() in [ar.name.lower() for ar in author_roles] else False
    
    # this method checks if a given user isn't already subsribed to a given pace
    async def is_user_subsribed(self, user_id, pace_label):
        resultset = await self.pg_con.fetchrow(f"SELECT COUNT(1) FROM public.fait_paces_users fpu INNER JOIN public.dim_paces dp on dp.\"PaceId\"=fpu.\"PaceId\" WHERE dp.\"PaceLabel\"='{pace_label}' AND fpu.\"DiscordId\"='{user_id}'")
        return False if resultset[0] == 0 else True

    # this method logs an action with the discord id of the user and the time at which it was done
    # time zone GMT+4
    async def log(self, user_id, time, action):
        print(f"user_id = {user_id} | time = {time} | action = {action}")
        await self.pg_con.execute(f"INSERT INTO public.log(\"DiscordId\", \"LogTime\", \"Action\") VALUES ('{user_id}', '{time}', '{action}')")
    
    # check if the value passed is valid, to avoid sql injections
    async def check_pace_label(self, pace_label):
        return True if re.match('^\w+$', pace_label) is not None else False
    
    # this method stores all current paces in a list to be used for autocompletion
    async def get_all_paces(self):
        con = await self.pg_con.acquire()
        resultset = await self.pg_con.fetch("SELECT DISTINCT \"PaceLabel\" FROM public.dim_paces")
        if len(resultset) > 0:
            self.paces_list.clear()
            for r in resultset:
                self.paces_list.append(r[0])
        await self.pg_con.release(con)
