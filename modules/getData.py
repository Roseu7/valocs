from datetime import datetime, timezone, timedelta
import math
from random import randint

from discord import Client, Embed, Colour, Interaction

from modules.createSupabaseClient import supabase_client
from num import NEED_PLAYER_NUM, MAP_LIST

supabase = supabase_client()

def get_player_list(db_id: int):
    res = (
        supabase.table("val-embed")
        .select("player_list")
        .eq("id", db_id)
        .execute()
    )
    player_list = res.data[0]["player_list"]
    return player_list if player_list is not None else []

def get_db_id(msg_id: int):
    res = (
        supabase.table("val-embed")
        .select("id")
        .eq("msg_id", msg_id)
        .execute()
    )
    return res.data[0]["id"]

def get_mmr(guild_id: int, user_id: int):
    try:
        res = (
            supabase.table("val-stats")
            .select("mmr")
            .eq("user_id", user_id)
            .eq("guild_id", guild_id)
            .execute()
        )
        return res.data[0]["mmr"]
    except IndexError:
        res = (
            supabase.table("val-stats")
            .insert({"user_id": user_id, "guild_id": guild_id, "mmr": int(500), "games_played": 0, "last_game_date": None})
            .execute()
        )
        return res.data[0]["mmr"]

def check_missing_players(inter: Interaction, player_list: list) -> list:
    missing_players = []
    for player_id in player_list:
        member = inter.guild.get_member(int(player_id))
        if member and not member.voice:
            missing_players.append(player_id)
    return missing_players

def get_task_time():
    time_list = []
    JST = timezone(timedelta(hours=+9), "JST")
    res = (
        supabase.table("val-embed")
        .select("start_time")
        .execute()
    )
    for i in range(len(res.data)):
        if res.data[i]["start_time"] != None:
            time_list.append(res.data[i]["start_time"])
    print(time_list)
    return time_list

def get_player_stats(guild_id: int, user_id: int):
    res = (
        supabase.table("val-stats")
        .select("*")
        .eq("guild_id", guild_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        new_player = {
            "mmr": 500,
            "user_id": user_id,
            "guild_id": guild_id,
            "games_played": 0,
            "last_game_date": None,
            "games_won": 0
        }
        supabase.table("val-stats").insert(new_player).execute()
        return new_player
    return res.data[0]

def update_player_stats(guild_id: int, user_id: int, player_stats: dict, mmr_change: int, is_winner):
    new_mmr = player_stats['mmr'] + mmr_change
    new_games_played = player_stats['games_played'] + 1
    new_wins = player_stats['games_won'] + (1 if is_winner else 0)
    current_time = datetime.now(timezone.utc)

    (
        supabase.table("val-stats")
        .update({
            "mmr": new_mmr,
            "games_played": new_games_played,
            "games_won": new_wins,
            "last_game_date": current_time.isoformat()
        })
        .eq("guild_id", guild_id)
        .eq("user_id", user_id)
        .execute()
    )

def calculate_mmr_change(player_stats: dict, is_winner: bool, team_mmr: float, enemy_mmr: float, winning_score: int, losing_score: int):
    base_change = 15
    mmr_diff_factor = (enemy_mmr - team_mmr) / 500  # MMR差による調整
    score_diff_factor = (winning_score - losing_score) / 13  # スコア差による調整
    games_played_factor = math.exp(-player_stats['games_played'] / 50)  # 試合数による調整
    mmr_change = base_change * (1 + mmr_diff_factor + score_diff_factor)
    mmr_change *= (1 + games_played_factor)  # 試合数が少ないほど変動が大きくなる
    if not is_winner:
        mmr_change *= -1
    return int(mmr_change)

def get_team_average_mmr(guild_id: int, team: list):
    total_mmr = sum(get_player_stats(guild_id, player)["mmr"] for player in team)
    return total_mmr / len(team)

def get_random_map():
    n = randint(0, 10)
    return MAP_LIST[n]

def ret_quick_embed(client: Client, db_id: int = None, author_id: int = None, event_id: int = None, event_user: int = None):
    player_list = []
    embed = Embed(color=Colour.brand_green())
    embed.add_field(name="\n", value="")

    if db_id != None:
        if event_id != None:
            user = client.get_user(event_user)
            #参加
            if event_id == 0:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"**プレイヤーが参加しました！**\n{user.mention}", inline=False)
            #退出
            elif event_id == 1:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"**プレイヤーが退出しました！**\n{user.mention}", inline=False)
            #募集開始
            elif event_id == 2:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"カスタムの募集を開始しました！", inline=False)
            #再度募集
            elif event_id == 3:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"カスタムの募集を再開しました！", inline=False)

        res = (
            supabase.table("val-embed")
            .select("*")
            .eq("id", db_id)
            .execute()
        )
        player_list = get_player_list(db_id)
        embed.add_field(name="\u200b", value="")
        embed.add_field(name="", value=f"__現在参加しているプレイヤー__ {len(player_list)}/{NEED_PLAYER_NUM}", inline=False)
        author_id = res.data[0]["author_id"]
        for i in range(len(player_list)):
            id = int(player_list[i])
            user = client.get_user(id)
            embed.add_field(name="", value=user.mention, inline=False)
        if len(player_list) >= NEED_PLAYER_NUM:
            embed.color = Colour.greyple()
        if len(player_list) < 8:
            for i in range(8 - len(player_list)):
                embed.add_field(name="\u200b", value="")

        if res.data[0]["start_time"] != None:
            embed.timestamp = datetime.fromtimestamp(int(res.data[0]["start_time"]))
        else:
            embed.set_footer(text="集まり次第開始")
            embed.timestamp = datetime.now()

    author = client.get_user(author_id)
    embed.set_author(name=f"カスタムマッチ #{db_id}", icon_url=author.avatar.url)

    return embed

def ret_standby_embed(client: Client, db_id: int = None, event_id: int = None, event_user: int = None):
    player_list = []

    embed = Embed(color=Colour.brand_green())

    if db_id != None:
        if event_id != None:
            user = client.get_user(event_user)
            #参加
            if event_id == 0:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"プレイヤーが参加しました！\n{user.mention}", inline=False)
            #退出
            elif event_id == 1:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"プレイヤーが退出しました！\n{user.mention}", inline=False)
            #自動退出
            elif event_id == 2:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"プレイヤーが退出しました！(自動)\n{user.mention}", inline=False)
            #再度募集
            elif event_id == 3:
                embed.add_field(name="\u200b", value="")
                embed.add_field(name="", value=f"カスタムの募集を再開しました！", inline=False)

        (
            supabase.table("val-embed")
            .select("*")
            .eq("id", db_id)
            .execute()
        )
        player_list = get_player_list(db_id)
        embed.add_field(name="\u200b", value="")
        embed.add_field(name="", value=f"__現在参加しているプレイヤー__ {len(player_list)}/{NEED_PLAYER_NUM}", inline=False)
        for i in range(len(player_list)):
            id = int(player_list[i][0])
            user = client.get_user(id)
            embed.add_field(name="", value=user.mention, inline=False)
        if len(player_list) >= NEED_PLAYER_NUM:
            embed.color = Colour.greyple()
        if len(player_list) < 8:
            for i in range(8 - len(player_list)):
                embed.add_field(name="\u200b", value="")

    embed.timestamp = datetime.now()
    embed.set_author(name=f"カスタムマッチ募集 #{db_id}")
    embed.set_footer(text=f"集まり次第開始")

    return embed

def ret_match_embed(client: Client, db_id: int = None):
    embed = Embed(color=Colour.blurple())
    res = (
        supabase.table("val-embed")
        .select("*")
        .eq("id", db_id)
        .execute()
    )

    if not res.data:
        embed.title = "エラー"
        embed.description = "データが見つかりません。"
        return embed

    team_a = res.data[0]["team_a"] or []
    team_b = res.data[0]["team_b"] or []
    embed.title = "チーム振り分け"
    embed.add_field(name="\u200b", value="")

    for team, team_name in [(team_a, "チームA"), (team_b, "チームB")]:
        team_text = ""
        if team:
            for player_id in team:
                player = client.get_user(int(player_id))
                if player:
                    mmr = get_mmr(res.data[0]["guild_id"], int(player_id))
                    team_text += f"{player.mention} (MMR: {mmr})\n"
                else:
                    team_text += f"Unknown Player ({player_id})\n"
        else:
            team_text = "メンバーなし"
        embed.add_field(name=team_name, value=team_text if team_text else "メンバーなし", inline=False)
        embed.add_field(name="\u200b", value="")

    return embed

def ret_result_embed(client: Client, guild_id: int, team_a: list, team_b: list, winning_team: str, score_a: int, score_b: int):
    embed = Embed(color=Colour.green())
    embed.title = f"試合結果: チーム{winning_team.upper()}の勝利 ({score_a} - {score_b})"
    embed.timestamp = datetime.now()
    embed.add_field(name="\u200b", value="")

    team_a_mmr = get_team_average_mmr(guild_id, team_a)
    team_b_mmr = get_team_average_mmr(guild_id, team_b)
    winning_score = max(score_a, score_b)
    losing_score = min(score_a, score_b)

    for team, team_name, enemy_mmr in [(team_a, "チームA", team_b_mmr), (team_b, "チームB", team_a_mmr)]:
        team_text = ""
        for player in team:
            player_stats = get_player_stats(guild_id, player)
            old_mmr = player_stats['mmr']
            is_winner = (winning_team == 'a' and team == team_a) or (winning_team == 'b' and team == team_b)
            mmr_change = calculate_mmr_change(
                player_stats,
                is_winner,
                get_team_average_mmr(guild_id, team),
                enemy_mmr,
                winning_score,
                losing_score
            )
            new_mmr = old_mmr + mmr_change
            team_text += f"{(client.get_user(player)).mention}: {old_mmr} → {new_mmr} ({mmr_change:+d})\n"
        embed.add_field(name=f"__{team_name}__", value=team_text, inline=False)
        embed.add_field(name="\u200b", value="")

    return embed
