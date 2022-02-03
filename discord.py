# มีไว้แอบ token ครับ 4 บรรทัดนี้ ไม่งั้นเดี๋ยวมันไม่ให้ผมเอาโค้ดลง github
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
from discord.ext import commands
import discord
from discord.utils import get
import youtube_dl
import asyncio
from async_timeout import timeout
from discord import FFmpegPCMAudio
from functools import partial
import itertools

youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn',
    # song will end if no this line
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            data = data['entries'][0]

        await ctx.send(f'```ini\n[ได้เพิ่มเพลง {data["title"]} เข้าในคิวแล้วว.]\n```')

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info,
                         url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data, requester=requester)


class MusicPlayer:
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog',
                 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                del players[self._guild]
                return await self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'ลองใส่ใหม่เหมือนจะเอ๋อเร่ออนาา.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(
                source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**กำลังเล่นเพลงนี้อยู่: ** `{source.title}` เพิ่มโดย '
                                               f'`{source.requester}`')
            await self.next.wait()
            source.cleanup()
            self.current = None

            try:
                await self.np.delete()
            except discord.HTTPException:
                pass

    async def destroy(self, guild):
        """Disconnect and cleanup the player."""
        await self._guild.voice_client.disconnect()
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class songAPI:
    def __init__(self):
        self.players = {}

    async def play(self, ctx, search: str):
        self.bot = ctx.bot
        self._guild = ctx.guild
        channel = ctx.author.voice.channel
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        if voice_client == None:
            await channel.connect()
            voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        await ctx.trigger_typing()

        _player = self.get_player(ctx)
        source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False)

        await _player.queue.put(source)

    players = {}

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    async def pause(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client == None:
            await ctx.channel.send("**```เข้าห้องพูดก่อนน้าา```**")
            return

        if voice_client.channel != ctx.author.voice.channel:
            await ctx.channel.send("**```กำลังเปิดเพลงให้ห้อง {0} อยู่```**".format(voice_client.channel))
            return

        voice_client.pause()

    async def resume(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client == None:
            await ctx.channel.send("**```เข้าห้องพูดก่อนน้าา```**")
            return

        if voice_client.channel != ctx.author.voice.channel:
            await ctx.channel.send("*```กำลังเปิดเพลงให้ห้อง {0} อยู่```**".format(voice_client.channel))
            return

        voice_client.resume()

    async def leave(self, ctx):
        del self.players[ctx.guild.id]
        await ctx.voice_client.disconnect()

    async def queueList(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        if voice_client == None or not voice_client.is_connected():
            await ctx.channel.send("**```เข้าห้องพูดก่อนน้าา```**", delete_after=10)
            return

        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('**```ไม่มีเพลงในคิว```**')

        # 1 2 3
        upcoming = list(itertools.islice(
            player.queue._queue, 0, player.queue.qsize()))
        fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
        embed = discord.Embed(
            title=f'**มี {len(upcoming)} เพลงในคิว**', description=fmt)
        await ctx.send(embed=embed)

    async def skip(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        if voice_client == None or not voice_client.is_connected():
            await ctx.channel.send("เข้าห้องพูดก่อนน้าา", delete_after=10)
            return

        if voice_client.is_paused():
            pass
        elif not voice_client.is_playing():
            return

        voice_client.stop()
        await ctx.send(f'**`{ctx.author}`**: ข้ามเพลงง!')

    async def volume(self, ctx, vol: float):

        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('เข้าห้องก่อนนะ!', delete_after=20)

        if not 0 < vol < 101:
            return await ctx.send('ใส่เลขระหว่าง 1 และ 100.')

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        await ctx.send(f'**`{ctx.author}`** **`: ได้ตั้งค่าระดับเสียงเป็น`** **`{vol}%`**')


load_dotenv()
token = os.getenv('TOKEN')

message_lastseen = datetime.now()
message2_lastseen = datetime.now()

bot = commands.Bot(command_prefix='--')

songsInstance = songAPI()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command(name="p", help="เล่นเพลงจาก youtube")
async def play(ctx, *, search: str):
    await songsInstance.play(ctx, search)


@bot.command(name='pause', help="หยุดเล่นเพลง")
async def pause(ctx):
    await songsInstance.pause(ctx)


@bot.command(name='resume', help="เล่นเพลงต่อจากเดิม")
async def resume(ctx):
    await songsInstance.resume(ctx)


@bot.command(name='leave', help='ออกห้อง')
async def leave(ctx):
    await songsInstance.leave(ctx)


@bot.command(name="q", help="โชว์เพลงในคิว")
async def queueList(ctx):
    await songsInstance.queueList(ctx)


@bot.command(name="n", help="เปลี่ยนเพลงแล้ว")
async def skip(ctx):
    await songsInstance.skip(ctx)


@bot.command(name="v", help="ปรับระดับเสียงเพลง")
async def volume(ctx, *, vol: float):
    await songsInstance.volume(ctx, vol)

bot.run(token)