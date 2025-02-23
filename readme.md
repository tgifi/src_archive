# Readme

Hacked together a script to help out with archiving leaderboard data, your milage may vary.

You can use the --run-index argument to tell it where to store its index file and the runs it downloads. All of those runs will go into a folder called runs. The video file name will include the game, category, run time, run date and players in a human readable format.

It is safe to kill the script and restart. The script will check if it already downloaded a video for a run. If the video was partially downloaded, it should be able to pick up where it left off as well.

You can use --lookup-game to find the SRC id for a game and --list-categories to find the categories for some game id. Use --download with a game_id and optionally a category id to start downloading run videos for that game. There's a good amount of configurability for what will be downloaded. Use --highlights-only to fetch only twitch videos. Use --keep-better to only keep runs equal or better than some rank (at the time they were submit). EX --keep-better 10, will get all runs that were ever top 10 runs. The --downgrade-quality command is similar, except instead of skipping runs it attempts to fetch a lower quality for runs over a certain rank. You can use both of these together to do things like keep all top 100 runs, but only fetch high quality videos for the top 10. If --current-only is used then the script will only look at current runs and use their current rank when deciding what to keep and what quality to fetch.

You can use the --list-missing instead of --download to list src links to runs for which you do not have a video.

You can also re-run the script with different sets of options. For example, you might do an initial run keeping only twitch highlights from current runs. Afterwards you might want to expand that to twitch highlights of any top ten run.

When the script checks to see if you already have a video, it does not consider the quality you're currently requesting or the quality of the saved video. So if you download low quality videos to start you won't be able to replace them in place with high quality videos.


# Examples

Lookup the SRC ID for a game:
```
    python3 src_archiver.py --lookup-game "Super Mario World"
```

Lookup the SRC IDs for a game's categories:
```
    python3 src_archiver.py -g pd0wq31e --list-categories
```

Download all runs for SMW:
```
    python3 src_archiver.py -g pd0wq31e --download
```

Download all 11 exit runs:
```
    python3 src_archiver.py -g pd0wq31e -c n2y1y72o --download
```

List the runs you have not downloaded for the 95 no cape historical top 10 twitch highlights
```
    python3 src_archiver.py -g pd0wq31e -c ndx31odq --list-missing --keep-better 10 --highlights-only
```

Download all current NSW runs with only twitch highlights:
```
    python3 src_archiver.py -g pd0wq31e -c zdn1jxkq --download --current-only --highlights-only
```

Download the world record history for 96 exit:
```
    python3 src_archiver.py -g pd0wq31e -c 7kjrn323 --download --keep-better 1
```

Download the current top 10 for all castles, prefer 480p or better if available and higher fps:
```
    python3 src_archiver.py -g pd0wq31e -c 02qjj79d --download --keep-better 10 -q "b;res:480,fps"
```

Download the top 100 history for small only preferring 720p or better for top 10 and 360p or worse for the rest:
```
    python3 src_archiver.py -g pd0wq31e -c n2y301do --download --keep-better 100 --downgrade-quality 10 -q "b;res:720" --low-quality "b;+res:360"
```
