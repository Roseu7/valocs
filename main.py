import os
import io
import pprint
import asyncio
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import Optional, Literal
from datetime import datetime, timedelta, timezone

from discord import Intents, Client, Interaction, app_commands, ButtonStyle, utils, Message, File, HTTPException, errors
from discord.app_commands import CommandTree, Group
from discord.ui import View, Button, Modal, TextInput
from discord.ext import tasks

from modules.convertDate import convert_date
from modules.createSupabaseClient import supabase_client
from modules.getData import get_player_list, get_db_id, ret_quick_embed, ret_standby_embed, calculate_mmr_change, get_mmr, get_team_average_mmr, get_player_stats, update_player_stats, get_random_map, ret_match_embed, ret_result_embed
from num import NEED_PLAYER_NUM

#Mainクラス
class Main(Client):
    #コンストラクタ
    def __init__(self, intents: Intents):
        super().__init__(intents=intents)
        self.tree = CommandTree(self)
        self.custom_match_in_progress = {}
        self.vc_move_lock = asyncio.Lock()
        self.join_locks = {}
        self.cooldowns = {}
        self.leaderboard = Leaderboard()

    #hookのセットアップ
    async def setup_hook(self):
        self.minute_task.start()
        self.tree.add_command(ValoCustom())
        self.tree.add_command(Stats())
        commands = await self.tree.sync()
        pprint.pprint(commands)

    async def on_ready(self):
        print("ログインしました")

    #インタラクションの際の振り分け
    async def on_interaction(self, inter: Interaction):
        try:
            if inter.data['component_type'] == 2:
                await self.on_button_click(inter)
        except KeyError:
            pass

    #ボタンをクリックした際のイベント
    async def on_button_click(self, inter: Interaction):
        custom_id = inter.data["custom_id"]

        if custom_id == "join_button":
            await self.handle_join_button(inter)
        elif custom_id == "leave_button":
            await self.handle_leave_button(inter)
        elif custom_id == "start_button":
            await self.handle_team_balance_button(inter)
        elif custom_id == "manual_team_balance":
            await self.handle_manual_team_balance(inter)
        elif custom_id == "join_team_a":
            await self.handle_join_team(inter, "A")
        elif custom_id == "join_team_b":
            await self.handle_join_team(inter, "B")
        elif custom_id == "leave_team":
            await self.handle_leave_team(inter)
        elif custom_id == "confirm_teams":
            await self.handle_confirm_teams(inter)
        elif custom_id == "mention_button":
            await self.handle_mention_button(inter)
        elif custom_id == "move_to_vc_button":
            await self.handle_move_to_vc_button(inter)
        elif custom_id == "random_map_button":
            await self.handle_random_map(inter)
        elif custom_id == "report_result":
            await self.handle_report_result_button(inter)
        elif custom_id == "reset_same_members":
            await self.handle_reset_same_members(inter)
        elif custom_id == "reset_new_members":
            await self.handle_reset_new_members(inter)
        elif custom_id == "disband":
            await self.handle_disband(inter)
        elif custom_id == "refresh_leaderboard":
            await self.handle_refresh_leaderboard(inter)

    #参加ボタン
    async def handle_join_button(self, inter: Interaction):
        msg_id = get_db_id(inter.message.id)
        if msg_id not in self.join_locks:
            self.join_locks[msg_id] = asyncio.Lock()

        async with self.join_locks[msg_id]:
            if self.custom_match_in_progress.get(msg_id, False):
                await inter.response.send_message(":warning:カスタムマッチが進行中のため退出できません。", ephemeral=True, delete_after=5)
                return
            player_list = get_player_list(get_db_id(inter.message.id))
            if player_list is None:
                player_list = []

            if any(inter.user.id == player if isinstance(player, int) else inter.user.id == player[0] for player in player_list):
                await inter.response.send_message(":warning:すでに参加しています", ephemeral=True, delete_after=5)
            else:
                await inter.response.send_message(":white_check_mark:参加しました", ephemeral=True, delete_after=5)
                res = (
                    supabase.table("val-embed")
                    .select("*")
                    .eq("msg_id", inter.message.id)
                    .execute()
                )
                if res.data[0]["is_standby"] == True:
                    if res.data[0]["dequeue_hour"] != None:
                        player_list.append([inter.user.id, int((datetime.now(timezone(timedelta(hours=+9), "JST"))+timedelta(hours=int(res.data[0]["dequeue_hour"]))).timestamp())])
                    else:
                        player_list.append([inter.user.id, int((datetime.now(timezone(timedelta(hours=+9), "JST"))).timestamp())])
                else:
                    player_list.append(inter.user.id)

                msg = await (client.get_channel(inter.channel_id)).fetch_message(inter.message.id)
                (
                    supabase.table("val-embed")
                    .update({"player_list": player_list})
                    .eq("msg_id", inter.message.id)
                    .execute()
                )
                view = JoinAndLeaveView(is_disabled=False)
                if res.data[0]["is_standby"] == True:
                    await msg.edit(embed=ret_standby_embed(self, res.data[0]["id"], 0, inter.user.id), view=view)
                else:
                    await msg.edit(embed=ret_quick_embed(self, res.data[0]["id"], None, 0, inter.user.id), view=view)
            if len(player_list) >= NEED_PLAYER_NUM:
                await view.update_button_state(msg, is_disabled=True)
                if res.data[0]["start_time"] == None:
                    res = (
                        supabase.table("val-embed")
                        .select("*")
                        .eq("msg_id", inter.message.id)
                        .execute()
                    )
                    await self.send_full_team_mention(res.data[0], msg)

    #退出ボタン
    async def handle_leave_button(self, inter: Interaction):
        msg_id = get_db_id(inter.message.id)
        if self.custom_match_in_progress.get(msg_id, False):
            await inter.response.send_message(":warning: カスタムマッチが進行中のため退出できません。", ephemeral=True)
            return

        player_list = get_player_list(get_db_id(inter.message.id))
        if inter.user.id in player_list or any(inter.user.id in l for l in player_list):
            await inter.response.send_message(":white_check_mark:退出しました", ephemeral=True, delete_after=5)
            msg = await (client.get_channel(inter.channel_id)).fetch_message(inter.message.id)
            res = (
                supabase.table("val-embed")
                .select("*")
                .eq("msg_id", inter.message.id)
                .execute()
            )
            if res.data[0]["is_standby"]:
                player_list = [l for l in player_list if inter.user.id not in l]
            else:
                player_list.remove(inter.user.id)
            (
                supabase.table("val-embed")
                .update({"player_list": player_list})
                .eq("msg_id", inter.message.id)
                .execute()
            )
            view = JoinAndLeaveView(is_disabled=False)
            if len(player_list) < NEED_PLAYER_NUM:
                await view.update_button_state(msg, is_disabled=False)
            else:
                await view.update_button_state(msg, is_disabled=True)
            if res.data[0]["is_standby"]:
                await msg.edit(embed=ret_standby_embed(self, res.data[0]["id"], 1, inter.user.id), view=view)
            else:
                await msg.edit(embed=ret_quick_embed(self, res.data[0]["id"], None, 1, inter.user.id), view=view)
        else:
            await inter.response.send_message(":warning:参加していません", ephemeral=True, delete_after=5)

    #自動振り分けボタン
    async def handle_team_balance_button(self, inter: Interaction):
        res = (
                supabase.table("val-embed")
                .select("*")
                .eq("mentioned_id", inter.message.id)
                .execute()
            )
        player_list = res.data[0]["player_list"]
        if len(player_list) < NEED_PLAYER_NUM:
            return await inter.response.send_message(f":warning:{NEED_PLAYER_NUM}人揃っていません", ephemeral=True, delete_after=5)
        else:
            missing_players = []
            for player in player_list:
                player_id = player[0] if isinstance(player, list) else player
                member = inter.guild.get_member(int(player_id))
                if member is None or member.voice is None:
                    missing_players.append(f"<@{player_id}>")
            if missing_players:
                missing_players_message = " ".join(missing_players)
                return await inter.response.send_message(f":warning:以下のプレイヤーがVCに参加していません:\n{missing_players_message}", ephemeral=True, delete_after=10)
            else:
                player_mmr = {}
                for player in player_list:
                    player_id = player[0] if isinstance(player, list) else player
                    mmr = get_mmr(inter.guild.id, int(player_id))
                    player_mmr[player_id] = mmr

                sorted_players = sorted(player_mmr.items(), key=lambda x: x[1], reverse=True)
                team_a = []
                team_b = []
                team_a_mmr = 0
                team_b_mmr = 0

                for i, (player_id, mmr) in enumerate(sorted_players):
                    if i % 2 == 0 or team_a_mmr <= team_b_mmr:
                        team_a.append(player_id)
                        team_a_mmr += mmr
                    else:
                        team_b.append(player_id)
                        team_b_mmr += mmr

                member = inter.guild.get_member(team_a[0])
                current_vc = member.voice.channel if member.voice else None

                if current_vc is None:
                    return await inter.response.send_message(":warning:参加者がVCにいません", ephemeral=True, delete_after=5)
                else:
                    msg = await inter.channel.send(embed=ret_match_embed(self, res.data[0]["id"]), view=MoveToVCView())
                    (
                        supabase.table("val-embed")
                        .update({"start_id": msg.id, "team_a": team_a, "team_b": team_b})
                        .eq("mentioned_id", inter.message.id)
                        .execute()
                    )
                    await msg.edit(embed=ret_match_embed(client, res.data[0]["id"]))
                    await inter.message.delete()

    #手動振り分けボタン
    async def handle_manual_team_balance(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("mentioned_id", inter.message.id)
            .execute()
        )
        player_list = res.data[0]["player_list"]
        if len(player_list) < NEED_PLAYER_NUM:
            return await inter.response.send_message(f":warning:{NEED_PLAYER_NUM}人揃っていません", ephemeral=True, delete_after=5)

        view = ManualTeamBalanceView()
        embed = ret_match_embed(client, res.data[0]["id"])
        await inter.response.edit_message(embed=embed, view=view)

    #チーム参加ボタン
    async def handle_join_team(self, inter: Interaction, team):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("mentioned_id", inter.message.id)
            .execute()
        )
        embed_data = res.data[0]
        team_a = embed_data["team_a"] or []
        team_b = embed_data["team_b"] or []
        player_id = inter.user.id
        if player_id in team_a or player_id in team_b:
            return await inter.response.send_message(":warning:既にチームに参加しています", ephemeral=True, delete_after=5)

        await inter.response.defer()
        if team == "A":
            team_a.append(player_id)
        else:
            team_b.append(player_id)
        (
            supabase.table("val-embed")
            .update({"team_a": team_a, "team_b": team_b})
            .eq("id", embed_data["id"])
            .execute()
        )

        view = ManualTeamBalanceView()
        await view.update_button_states(team_a, team_b)
        await self.update_team_display(inter, embed_data["id"])
        await inter.followup.send(f":white_check_mark:チーム{team}に参加しました", ephemeral=True)

    #チーム退出ボタン
    async def handle_leave_team(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("mentioned_id", inter.message.id)
            .execute()
        )
        embed_data = res.data[0]
        team_a = embed_data.get("team_a", []) or []
        team_b = embed_data.get("team_b", []) or []
        player_id = inter.user.id
        if player_id in team_a:
            team_a.remove(player_id)
        elif player_id in team_b:
            team_b.remove(player_id)
        else:
            return await inter.response.send_message(":warning:チームに参加していません", ephemeral=True, delete_after=5)

        await inter.response.defer()
        (
            supabase.table("val-embed")
            .update({"team_a": team_a, "team_b": team_b})
            .eq("id", embed_data["id"])
            .execute()
        )
        view = ManualTeamBalanceView()
        await view.update_button_states(team_a, team_b)
        await self.update_team_display(inter, embed_data["id"])
        await inter.followup.send(f":white_check_mark:チームから退出しました", ephemeral=True)

    #チーム確定ボタン
    async def handle_confirm_teams(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("mentioned_id", inter.message.id)
            .execute()
        )
        if len(res.data[0]["team_a"]) + len(res.data[0]["team_b"]) != NEED_PLAYER_NUM:
            return await inter.response.send_message(":warning:チームが完成していません", ephemeral=True, delete_after=5)

        await inter.response.defer()
        msg = await inter.channel.send(embed=ret_match_embed(self, res.data[0]["id"]), view=MoveToVCView())
        (
            supabase.table("val-embed")
            .update({"start_id": msg.id})
            .eq("mentioned_id", inter.message.id)
            .execute()
        )
        await inter.message.delete()

    #メンションボタン
    async def handle_mention_button(self, inter: Interaction):
        res = (
                supabase.table("val-embed")
                .select("*")
                .eq("mentioned_id", inter.message.id)
                .execute()
            )
        player_list = res.data[0]["player_list"]
        missing_players = []

        for player in player_list:
            player_id = player[0] if isinstance(player, list) else player
            member = inter.guild.get_member(int(player_id))
            if member and not member.voice:
                missing_players.append(member)

        if missing_players:
            await inter.response.send_message(f"VCに未参加のプレイヤー:\n{' '.join([member.mention for member in missing_players])}", delete_after=60)
        else:
            await inter.response.send_message(":warning:すべてのプレイヤーがVCに参加済みです", ephemeral=True, delete_after=5)

    #VC移動ボタン
    async def handle_move_to_vc_button(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("start_id", inter.message.id)
            .execute()
        )
        msg_id = res.data[0]["msg_id"]
        async with self.vc_move_lock:
            if self.custom_match_in_progress.get(msg_id, False):
                await inter.response.send_message(":warning:VCの移動は既に実行されています", ephemeral=True, delete_after=5)
                return
            self.custom_match_in_progress[msg_id] = True
            team_a = res.data[0]["team_a"]
            team_b = res.data[0]["team_b"]

            current_vc = inter.guild.get_channel(inter.user.voice.channel.id)
            supabase.table("val-embed").update({"origin_vc_name": current_vc.name}).eq("start_id", inter.message.id).execute()
            await current_vc.edit(name="チームA")
            team_b_vc = await inter.guild.create_voice_channel(name="チームB", category=current_vc.category, position=current_vc.position+1)

            for player_id in team_b:
                member = inter.guild.get_member(int(player_id))
                if member and member.voice:
                    await member.move_to(team_b_vc)

            await inter.message.edit(view=TeamResultView())
            await inter.response.send_message("VCの移動が完了しました。", ephemeral=True, delete_after=5)
            self.custom_match_in_progress[msg_id] = False

    #ランダムマップボタン
    async def handle_random_map(self, inter: Interaction):
        random_map = get_random_map()
        await inter.response.send_message(f"{random_map[0]}", file=File(random_map[1]), delete_after=30)

    #結果報告ボタン
    async def handle_report_result_button(self, inter: Interaction):
        await inter.response.send_modal(ResultModal())

    #同じメンバーでリセットボタン
    async def handle_reset_same_members(self, inter: Interaction):
        await inter.response.defer()
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("reset_id", inter.message.id)
            .execute()
        )
        player_list = res.data[0]["player_list"]
        msg = await (client.get_channel(inter.channel_id)).fetch_message(res.data[0]["msg_id"])
        mention_message = await self.send_full_team_mention(res.data[0], msg)
        (
            supabase.table("val-embed")
            .update({
                "player_list": player_list,
                "mentioned_id": mention_message.id,
                "is_mentioned": True,
                "start_id": None,
                "reset_id": None,
                "team_a": None,
                "team_b": None
            })
            .eq("id", res.data[0]["id"])
            .execute()
        )
        await inter.message.delete()
        await inter.followup.send("同じメンバーで再開します", ephemeral=True)

    #違うメンバーでリセットボタン
    async def handle_reset_new_members(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("reset_id", inter.message.id)
            .execute()
        )
        (
            supabase.table("val-embed")
            .update({"player_list": [], "msg_id": msg.id, "is_mentioned": False})
            .eq("id", res.data[0]["id"])
            .execute()
        )
        if res.data[0]["is_standby"]:
            msg = await (inter.channel).fetch_message(res.data[0]["msg_id"])
            msg.edit(embed=ret_standby_embed(self, res.data[0]["id"]), view=JoinAndLeaveView(is_disabled=False))
        else:
            msg = await (inter.channel).fetch_message(res.data[0]["msg_id"])
            await msg.delete()
            msg = await inter.channel.send(embed=ret_quick_embed(self, res.data[0]["id"], None, 3), view=JoinAndLeaveView(is_disabled=False))
        await inter.message.delete()
        await inter.response.send_message("新しいメンバーで再開しました。", ephemeral=True, delete_after=5)

    #解散ボタン
    async def handle_disband(self, inter: Interaction):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("reset_id", inter.message.id)
            .execute()
        )
        msg_id = res.data[0]["id"]
        self.custom_match_in_progress.pop(msg_id, None)
        msg = await inter.channel.fetch_message(res.data[0]["msg_id"])
        if res.data[0]["is_standby"]:
            (
                supabase.table("val-embed")
                .update({"player_list": [], "is_mentioned": False, "mentioned_id": None, "reset_id": None, "team_a": None, "team_b": None})
                .eq("id", res.data[0]["id"])
                .execute()
            )
            await msg.edit(embed=ret_standby_embed(self, res.data[0]["id"]), view=JoinAndLeaveView(is_disabled=False))
        else:
            await msg.delete()
            (
                supabase.table("val-embed")
                .delete()
                .eq("id", res.data[0]["id"])
                .execute()
            )
        await inter.message.delete()
        await inter.response.send_message(":white_check_mark:カスタムを解散しました。", ephemeral=True, delete_after=5)

    #standbyの場合の定期実行
    async def handle_standby_embed(self, embed_data):
        try:
            msg = await (client.get_channel(embed_data["ch_id"])).fetch_message(embed_data["msg_id"])
            if embed_data["dequeue_hour"] is not None:
                await self.handle_auto_dequeue(embed_data, msg)
        except errors.NotFound:
            pass
        except Exception as e:
            print(e)

    #quickの場合の定期実行
    async def handle_quick_embed(self, embed_data):
        current_time = int(datetime.now().timestamp())
        if embed_data["start_time"] and int(embed_data["start_time"]) <= current_time:
            try:
                msg = await (client.get_channel(embed_data["ch_id"])).fetch_message(embed_data["msg_id"])
                player_list = embed_data["player_list"] or []
                if len(player_list) < NEED_PLAYER_NUM and not embed_data.get("is_quick_mention_sent", False):
                    await self.send_recruitment_mention(embed_data, msg)
                    (
                        supabase.table("val-embed")
                        .update({"is_quick_mention_sent": True})
                        .eq("id", embed_data["id"])
                        .execute()
                    )
                elif len(player_list) >= NEED_PLAYER_NUM and embed_data.get("is_quick_mention_sent", False):
                    (
                        supabase.table("val-embed")
                        .update({"is_quick_mention_sent": False})
                        .eq("id", embed_data["id"])
                        .execute()
                    )
            except errors.NotFound:
                pass
            except Exception as e:
                print(e)

    #時間による自動退出
    async def handle_auto_dequeue(self, embed_data, msg):
        if embed_data["player_list"]:
            standby_player = embed_data["player_list"]
            current_time = int(datetime.now(timezone(timedelta(hours=+9), "JST")).timestamp())
            updated_standby_player = []
            removed_users = []

            for player in standby_player:
                if isinstance(player, list) and len(player) == 2:
                    user_id, join_time = player
                    if join_time <= current_time:
                        removed_users.append(user_id)
                    else:
                        updated_standby_player.append(player)
                elif isinstance(player, (int, str)):
                    updated_standby_player.append(player)

            if len(updated_standby_player) < len(standby_player):
                (
                    supabase.table("val-embed")
                    .update({"player_list": updated_standby_player})
                    .eq("id", embed_data["id"])
                    .execute()
                )
                view = JoinAndLeaveView(is_disabled=False)
                if len(updated_standby_player) < NEED_PLAYER_NUM:
                    await view.update_button_state(msg, is_disabled=False)
                else:
                    await view.update_button_state(msg, is_disabled=True)
                for user in removed_users:
                    await msg.edit(embed=ret_standby_embed(self, embed_data["id"], 2, user), view=view)

    #メンバー集まっているか確認
    async def check_and_mention_full_team(self, embed_data, msg):
        if embed_data["player_list"] and len(embed_data["player_list"]) >= NEED_PLAYER_NUM:
            if embed_data["is_mentioned"] == False:
                await self.send_full_team_mention(embed_data, msg)

    #参加メンバーメンションを送る
    async def send_full_team_mention(self, embed_data, msg: Message):
        message = []
        for player in embed_data["player_list"]:
            player_id = player[0] if isinstance(player, list) else player
            message.append(f"{(client.get_user(player_id)).mention}")
        new_msg = await msg.reply(f"{' '.join(message)}\nメンバーが集まりました", view=BalanceAndMentionView())
        (
            supabase.table("val-embed")
            .update({"is_mentioned": True, "mentioned_id": new_msg.id})
            .eq("id", embed_data["id"])
            .execute()
        )
        return new_msg

    #人数が足りない場合
    async def send_recruitment_mention(self, embed_data, msg: Message):
        needed_players = NEED_PLAYER_NUM - len(embed_data["player_list"] or [])
        new_msg = await msg.reply(f"@everyone\n@{needed_players}人募集中")
        (
            supabase.table("val-embed")
            .update({"mentioned_id": new_msg.id})
            .eq("id", embed_data["id"])
            .execute()
        )

    #チーム振り分け表示を更新
    async def update_team_display(self, inter: Interaction, db_id):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("id", db_id)
            .execute()
        )
        embed_data = res.data[0]

        embed = ret_match_embed(client, db_id)
        view = ManualTeamBalanceView()
        await view.update_button_states(embed_data["team_a"] or [], embed_data["team_b"] or [])
        await inter.message.edit(embed=embed, view=view)

    async def handle_refresh_leaderboard(self, inter: Interaction):
        guild_id = str(inter.guild.id)
        current_time = datetime.now().timestamp()

        if guild_id in self.cooldowns:
            time_diff = current_time - self.cooldowns[guild_id]
            if time_diff < 10:  # 10秒のクールダウン
                await inter.response.send_message(f"クールダウン中です。{10 - time_diff:.2f}秒後に再試行してください。", ephemeral=True, delete_after=5)
                return

        self.cooldowns[guild_id] = current_time

        leaderboard_image = await self.leaderboard.create_leaderboard_image(inter.guild)
        await inter.message.edit(attachments=[File(leaderboard_image, filename="leaderboard.png")])

        supabase.table("val-lb").update({"last_updated": datetime.now().isoformat()}).eq("guild_id", inter.guild.id).execute()

        await inter.response.send_message("リーダーボードを更新しました。", ephemeral=True, delete_after=5)

    #定期実行
    @tasks.loop(minutes=1)
    async def minute_task(self):
        if datetime.now().second < 7:
            print(f"定期実行: {datetime.now()}")
            rate_limited, retry_after = await self.is_rate_limited()
            if rate_limited:
                print(f"レート制限中 残り{retry_after}秒")
            try:
                res = (
                    supabase.table("val-embed")
                    .select("*")
                    .execute()
                )
                for embed_data in res.data:
                    if embed_data["is_standby"] == True:
                        await self.handle_standby_embed(embed_data)
                    else:
                        await self.handle_quick_embed(embed_data)
                        current_player_list = embed_data["player_list"] or []
                        previous_player_list = embed_data["previous_player_list"] or []
                        if set(current_player_list) != set(previous_player_list):
                            (
                                supabase.table("val-embed")
                                .update({
                                    "previous_player_list": current_player_list,
                                    "is_quick_mention_sent": False
                                })
                                .eq("id", embed_data["id"])
                                .execute()
                            )
            except HTTPException as e:
                if e.status == 429:
                    retry_after = int(e.response.headers.get('Retry-After', 0))
                    print(f"レート制限中 残り{retry_after}秒")
                    await asyncio.sleep(retry_after)
                else:
                    print(e)

    #定期実行の秒数を調整
    @minute_task.before_loop
    async def before_minute_task(self):
        await self.wait_until_ready()
        now = datetime.now()
        next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep((next_minute - now).total_seconds())

    #レート上限確認
    async def is_rate_limited(self):
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.http.token}"
                }
                async with session.get("http://discord.com/api/v10/users/@me", headers=headers) as response:
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 0))
                        return True, retry_after
                    else:
                        return False, 0

#Valoカスタムコマンド
class ValoCustom(Group):
    #コンストラクタ
    def __init__(self):
        super().__init__(name="valocs", description="VALORANTのカスタム用のコマンドです")

    #quick募集コマンド
    @app_commands.command(name="quick", description="新しくカスタムマッチのメンバーを募集します")
    @app_commands.rename(time="開始時間", is_ranked="mmr変動")
    @app_commands.describe(time="開始する時間を半角数字で指定してください。(例: 7月10日20時 → 07102000)", is_ranked="MMRをを変動させるかどうかを選択してください。")
    async def quick(self, inter: Interaction, time: str = None, is_ranked: Optional[Literal["あり", "なし"]] = "あり"):
        if time != None:
            try:
                unix_time = convert_date(time)
            except ValueError as e:
                await inter.response.send_message(f"{e}", ephemeral=True)
                return
        else:
            unix_time = None
        await inter.response.send_message(embed=ret_quick_embed(client, None, inter.user.id, 2), view=JoinAndLeaveView(is_disabled=False), ephemeral=False)
        custom_embed = await inter.original_response()
        if is_ranked == "あり":
            is_ranked = True
        else:
            is_ranked = False
        res = (
            supabase.table("val-embed")
            .insert({
                "guild_id": int(inter.guild.id),
                "msg_id": int(custom_embed.id),
                "author_id": int(inter.user.id),
                "start_time": unix_time,
                "is_standby": False,
                "ch_id": int(inter.channel.id),
                "is_mentioned": False,
                "is_quick_mention_sent": False,
                "is_ranked": is_ranked,
                "previous_player_list": []})
            .execute()
        )
        await custom_embed.edit(embed=ret_quick_embed(client, res.data[0]["id"]))

    #standby募集コマンド
    @app_commands.command(name="standby", description="常時募集メッセージを新たに作成します")
    @app_commands.rename(dequeue_hour="自動退出時間", is_ranked="mmr変動")
    @app_commands.describe(dequeue_hour="自動的に退出させる時間を指定できます(半角数字、1以上)", is_ranked="MMRをを変動させるかどうかを選択してください。")
    async def standby(self, inter: Interaction, dequeue_hour: int = None, is_ranked: Optional[Literal["あり", "なし"]] = "あり"):
        if dequeue_hour == 0:
            await inter.response.send_message(":warning:自動退出時間の指定は1以上にしてください", ephemeral=True, delete_after=5)
            return
        view = JoinAndLeaveView(is_disabled=False)
        await inter.response.send_message(":white_check_mark:常時募集メッセージを作成しています...", ephemeral=True)
        msg = await inter.channel.send("作成中", view=view)
        if is_ranked == "あり":
            is_ranked = True
        else:
            is_ranked = False
        res = (
            supabase.table("val-embed")
            .insert({
                "guild_id": int(inter.guild.id),
                "msg_id": int(msg.id),
                "author_id": int(inter.user.id),
                "is_standby": True,
                "ch_id": int(inter.channel.id),
                "dequeue_hour": dequeue_hour,
                "is_mentioned": False,
                "is_ranked": is_ranked})
            .execute()
        )
        await msg.edit(content="", embed=ret_standby_embed(client, res.data[0]["id"]))
        await inter.edit_original_response(content=":white_check_mark:常時募集メッセージを作成しました")

    #募集削除コマンド
    @app_commands.command(name="delete", description="カスタムマッチの募集を削除します")
    @app_commands.rename(id="募集id")
    @app_commands.describe(id="削除する募集IDを入力してください(半角数字)")
    async def delete(self, inter: Interaction, id: int):
        res = (
            supabase.table("val-embed")
            .select("author_id")
            .eq("id", id)
            .execute()
        )
        if id == None:
            await inter.response.send_message(":warning:募集IDを入力してください", ephemeral=True, delete_after=5)
            return
        elif res.data == (None or []):
            await inter.response.send_message(":warning:募集IDが存在しません", ephemeral=True, delete_after=5)
            return
        elif res.data[0]["author_id"] == inter.user.id:
            msg_id = (
                supabase.table("val-embed")
                .select("msg_id")
                .eq("id", id)
                .execute()
            )
            msg = await (client.get_channel(inter.channel_id)).fetch_message(msg_id.data[0]["msg_id"])
            (
                supabase.table("val-embed")
                .delete()
                .eq("id", id)
                .execute()
            )
            await msg.delete()
            await inter.response.send_message(f":white_check_mark:ID: {id} の募集を削除しました", ephemeral=True, delete_after=5)
        else:
            await inter.response.send_message(":warning:この募集はあなたが作成したものではありません", ephemeral=True, delete_after=5)

class Leaderboard(Group):
    def __init__(self):
        super().__init__(name="lb", description="リーダーボード関連のコマンドです")
        self.layout = {
            'start_y': 77,
            'row_height': 174,
            'mmr_x': 308,
            'icon_x': 452,
            'name_x': 586,
            'games_won_x': 1221
        }

    @app_commands.command(name="show", description="リーダーボードを表示します")
    async def show(self, inter: Interaction):
        await inter.response.defer(ephemeral=True)
        leaderboard_image = await self.create_leaderboard_image(inter.guild)
        await inter.followup.send(file=File(leaderboard_image, filename="leaderboard.png"))

    @app_commands.command(name="new", description="固定のリーダーボードを作成します")
    @app_commands.checks.has_permissions(administrator=True)
    async def new(self, inter: Interaction):
        existing_lb = supabase.table("val-lb").select("*").eq("guild_id", inter.guild.id).execute()
        if existing_lb.data:
            await inter.response.send_message(f":warning:このサーバーには既にリーダーボードが存在します。", ephemeral=True, delete_after=5)
            return

        leaderboard_image = await self.create_leaderboard_image(inter.guild)
        message = await inter.channel.send(file=File(fp=leaderboard_image, filename="lb.png"), view=LeaderboardRefreshView())
        (
            supabase.table("val-lb")
            .insert({
                "guild_id": inter.guild.id,
                "ch_id": inter.channel.id,
                "msg_id": message.id,
                "last_updated": datetime.now().isoformat()})
            .execute()
        )
        await inter.response.send_message(f":white_check_mark:リーダーボードを作成しました。", ephemeral=True, delete_after=5)

    @app_commands.command(name="delete", description="固定のリーダーボードを削除します")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete(self, inter: Interaction):
        lb_data = supabase.table("val-lb").select("*").eq("guild_id", inter.guild.id).execute()
        if not lb_data.data:
            await inter.response.send_message(":warning:このサーバーにはリーダーボードが存在しません。", ephemeral=True, delete_after=5)
            return

        channel = inter.guild.get_channel(lb_data.data[0]["ch_id"])
        if channel:
            try:
                message = await channel.fetch_message(lb_data.data[0]["msg_id"])
                await message.delete()
            except errors.NotFound:
                pass

        supabase.table("val-lb").delete().eq("guild_id", inter.guild.id).execute()
        await inter.response.send_message(":white_check_mark:リーダーボードを削除しました。", ephemeral=True, delete_after=5)

    async def get_player_avatar(self, member):
        if member.avatar:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(member.avatar.url)) as resp:
                    if resp.status == 200:
                        return await resp.read()
        return None

    async def create_leaderboard_image(self, guild):
        with Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "lb.png")) as bg:
            image = bg.copy().convert('RGBA')

        draw = ImageDraw.Draw(image)

        players = (
            supabase.table("val-stats")
            .select("*")
            .eq("guild_id", guild.id)
            .order("mmr", desc=True)
            .limit(10)
            .execute()
        ).data

        for i, player in enumerate(players):
            member = guild.get_member(player["user_id"])
            if member:
                y = self.layout['start_y'] + i * self.layout['row_height']

                # MMR
                font = ImageFont.truetype(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "Inter-Regular.ttf"), 36)
                draw.text((self.layout['mmr_x'], y), str(player['mmr']), font=font, fill=(255,255,255))

                # アバター
                avatar_data = await self.get_player_avatar(member)
                if avatar_data:
                    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
                    avatar = avatar.resize((96, 96))
                    image.paste(avatar, (self.layout['icon_x'], y-26), avatar)

                # 名前
                draw.text((self.layout['name_x'], y), member.name, font=font, fill=(255,255,255))

                # 勝利数
                font = ImageFont.truetype(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "Inter-Regular.ttf"), 24)
                draw.text((self.layout['games_won_x'], y+7), str(player['games_won']), font=font, fill=(255,255,255))

        final_image = io.BytesIO()
        image.save(final_image, format='PNG')
        final_image.seek(0)

        return final_image

#stats取得コマンド
class Stats(Group):
    #コンストラクタ
    def __init__(self):
        super().__init__(name="stats", description="スタッツ関連のコマンドです")
        self.add_command(Leaderboard())

    #MMR取得コマンド
    @app_commands.command(name="mmr", description="現在あなたがいるサーバーのMMRを確認します")
    async def mmr(self, inter: Interaction):
        await inter.response.send_message(f"現在のMMR: {get_mmr(inter.guild.id, inter.user.id)}", ephemeral=True)

#参加・退出ボタン
class JoinAndLeaveView(View):
    #コンストラクタ
    def __init__(self, is_disabled: bool):
        super().__init__()
        self.add_item(Button(label="参加する", style=ButtonStyle.primary, custom_id="join_button", disabled=is_disabled))
        self.add_item(Button(label="退出する", style=ButtonStyle.danger, custom_id="leave_button", disabled=is_disabled))

    #参加ボタンの状態変更
    async def update_button_state(self, msg, is_disabled: bool):
        view = JoinAndLeaveView(is_disabled)
        await msg.edit(view=view)

#振り分け・メンションボタン
class BalanceAndMentionView(View):
    #コンストラクタ
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="自動チーム分け", style=ButtonStyle.primary, custom_id="start_button"))
        self.add_item(Button(label="手動チーム分け", style=ButtonStyle.secondary, custom_id="manual_team_balance"))
        self.add_item(Button(label="VCにいないプレイヤーをメンション", style=ButtonStyle.secondary, custom_id="mention_button"))

#手動振り分けボタン
class ManualTeamBalanceView(View):
    #コンストラクタ
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="チームAに参加", style=ButtonStyle.primary, custom_id="join_team_a"))
        self.add_item(Button(label="チームBに参加", style=ButtonStyle.primary, custom_id="join_team_b"))
        self.add_item(Button(label="チームを抜ける", style=ButtonStyle.secondary, custom_id="leave_team"))
        self.add_item(Button(label="チーム確定", style=ButtonStyle.success, custom_id="confirm_teams", disabled=True))

    #ボタンの状態変更
    async def update_button_states(self, team_a, team_b):
        team_size = NEED_PLAYER_NUM // 2
        self.children[0].disabled = len(team_a) >= team_size
        self.children[1].disabled = len(team_b) >= team_size
        self.children[2].disabled = len(team_a) + len(team_b) == 0
        self.children[3].disabled = len(team_a) + len(team_b) != NEED_PLAYER_NUM

#VCを移動ボタン
class MoveToVCView(View):
    #コンストラクタ
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="VCを移動", style=ButtonStyle.primary, custom_id="move_to_vc_button"))
        self.add_item(Button(label="ランダムマップ", style=ButtonStyle.secondary, custom_id="random_map_button"))

#結果報告ボタン
class TeamResultView(View):
    #コンストラクタ
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="試合結果報告", style=ButtonStyle.green, custom_id="report_result"))

#リセットボタン
class ResetView(View):
    #コンストラクタ
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="同じメンバーで続ける", style=ButtonStyle.primary, custom_id="reset_same_members"))
        self.add_item(Button(label="メンバーを変更して続ける", style=ButtonStyle.secondary, custom_id="reset_new_members"))
        self.add_item(Button(label="カスタムを解散する", style=ButtonStyle.danger, custom_id="disband"))

#結果報告フィールド
class ResultModal(Modal, title="試合結果報告"):
    winning_team = TextInput(label="勝利チーム", placeholder="例: A", required=True, min_length=1, max_length=1)
    score_a = TextInput(label="チームAの取得ラウンド数", placeholder="例: 13", required=True, min_length=1, max_length=2)
    score_b = TextInput(label="チームBの取得ラウンド数", placeholder="例: 11", required=True, min_length=1, max_length=2)

    #送信時
    async def on_submit(self, inter: Interaction):
        winning_team = self.winning_team.value.lower()
        try:
            score_a = int(self.score_a.value)
            score_b = int(self.score_b.value)
        except ValueError:
            await inter.response.send_message(":warning:ラウンド数には半角数字を入力してください", ephemeral=True, delete_after=5)
            return

        if winning_team not in ['a', 'b']:
            await inter.response.send_message(":warning:勝利チームにはAかBを半角英字で入力してください", ephemeral=True, delete_after=5)
            return

        if not self.is_valid_score(winning_team, score_a, score_b):
            await inter.response.send_message(":warning:無効なスコアです。正しいスコアを入力してください。", ephemeral=True, delete_after=5)
            return

        await inter.response.defer()
        team_a, team_b = self.get_team_members(inter.message.id)
        result_embed = ret_result_embed(inter.client, inter.guild.id, team_a, team_b, winning_team, score_a, score_b)
        await inter.channel.send(embed=result_embed, delete_after=120)
        self.update_mmr(inter.guild, team_a, team_b, winning_team, score_a, score_b)
        await self.handle_voice_channels(inter.guild, inter.message.id)
        await inter.message.delete()
        msg = await inter.channel.send("次のマッチについて選択してください", view=ResetView())
        (
            supabase.table("val-embed")
            .update({"reset_id": msg.id})
            .eq("start_id", inter.message.id)
            .execute()
        )

    #スコアの整合性確認
    def is_valid_score(self, winning_team, score_a, score_b):
        return (
            (winning_team == 'a' and score_a > score_b) or
            (winning_team == 'b' and score_b > score_a)
        ) and (
            max(score_a, score_b) >= 13 and
            min(score_a, score_b) >= 0 and
            (max(score_a, score_b) < 14 or abs(score_a - score_b) == 2) and
            not (score_a == 13 and score_b == 12) and
            not (score_a == 12 and score_b == 13) and
            not (score_a == 13 and score_b > 11 and score_b != 15) and
            not (score_b == 13 and score_a > 11 and score_a != 15)
        )

    #チームメンバー取得
    def get_team_members(self, start_id):
        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("start_id", start_id)
            .execute()
        )
        return res.data[0]["team_a"], res.data[0]["team_b"]

    #MMR変動
    def update_mmr(self, guild, team_a, team_b, winning_team, score_a, score_b):
        all_players = team_a + team_b
        winning_score = score_a if winning_team == 'a' else score_b
        losing_score = score_b if winning_team == 'a' else score_a

        team_a_mmr = get_team_average_mmr(guild.id, team_a)
        team_b_mmr = get_team_average_mmr(guild.id, team_b)

        for player in all_players:
            player_stats = get_player_stats(guild.id, player)
            is_winner = winning_team == ('a' if player in team_a else 'b')
            mmr_change = calculate_mmr_change(
                player_stats,
                is_winner,
                team_a_mmr,
                team_b_mmr,
                winning_score,
                losing_score
            )
            update_player_stats(guild.id, player, player_stats, mmr_change, is_winner)

    #VC修正
    async def handle_voice_channels(self, guild, msg_id):
        original_vc = utils.get(guild.voice_channels, name="チームA")
        team_b_vc = utils.get(guild.voice_channels, name="チームB")

        if original_vc and team_b_vc:
            for member in team_b_vc.members:
                await member.move_to(original_vc)
            res = (supabase.table("val-embed").select("*").eq("start_id", msg_id).execute())
            await team_b_vc.delete()
            await original_vc.edit(name=f"{res.data[0]["origin_vc_name"]}")

class LeaderboardRefreshView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="更新", style=ButtonStyle.primary, custom_id="refresh_leaderboard"))

#token取得
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
#intents設定
intents = Intents.default()
intents.members = True
intents.message_content = True
#client設定
client = Main(intents=intents)
supabase = supabase_client()

#pingコマンド
@client.tree.command(name="ping", description="動作確認用")
async def ping(inter: Interaction):
  await inter.response.send_message(f"pong({round(client.latency * 1000)}ms)", ephemeral=True)

#bot起動
client.run(DISCORD_TOKEN)
