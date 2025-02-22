import argparse
from bisect import bisect
import json
import requests
import sys
import yt_dlp
from collections import defaultdict
from datetime import datetime
from os import listdir, makedirs
from os.path import isfile
from time import time, sleep

BASE_URL = "https://www.speedrun.com/api/v1"
MAX_ELEMENTS = 200
INDEX_FILE = "runs_index.json"
RUNS_FOLDER = "runs"


class VideoDownloadException(Exception):
    """An error raised when fetching a video."""


class RateLimitedRequest:
    """
    Gets with a rate limit.

    SRC enforces a limit of 100 requests in the last minute. This is kind of
    conservative, but I don't see much value in briefly allowing faster
    requests. If we want to do lots of requests, we'll be pushed down to this
    rate. If there's only a few requests, the delay doesn't really matter.
    """

    def __init__(self):
        self.last_request = time()

    def get(self, url, params=None):
        """Do a get."""
        if params is None:
            params = {}
        now = time()
        delay = now - self.last_request
        min_delay = (60 / 100) + 0.1
        if delay < min_delay:
            sleep(min_delay - delay)
        try:
            result = requests.get(url, params).json()
            # If we got rate limited take a break and retry.
            if result.get("status") == 420:
                sleep(60)
                result = requests.get(url, params).json()
            now = time()
        except:
            sleep(1)
            raise Exception("Rate limiting")
        self.last_request = now
        return result


class SRCIDsToNames:
    """Cache a human readable name for SRC ids."""

    def __init__(self):
        self.id_to_name = {}

    def _get_sub_categories(self, category_id, requester: RateLimitedRequest):
        sub_category_key = f"{category_id}_sub_categories"
        if sub_category_key in self.id_to_name:
            return self.id_to_name[sub_category_key]
        url = f"{BASE_URL}/categories/{category_id}/variables"
        resp = requester.get(url)["data"]
        sub_categories = {}
        for variable in resp:
            if not variable.get("is-subcategory") or not variable.get("mandatory"):
                continue
            sub_categories[variable["id"]] = {}
            for value_id, value_data in variable["values"]["values"].items():
                sub_categories[variable["id"]][value_id] = value_data["label"]
        self.id_to_name[sub_category_key] = sub_categories
        return sub_categories

    def fetch_game(self, game_id, requester):
        if game_id in self.id_to_name:
            return self.id_to_name[game_id]
        url = f"{BASE_URL}/games/{game_id}"
        resp = requester.get(url, {})["data"]
        name = "_".join(resp["names"]["international"].split())
        self.id_to_name[game_id] = name
        return name

    def fetch_category(self, run, requester):
        category_id = run["category"]
        if category_id in self.id_to_name:
            name = self.id_to_name[category_id]
        else:
            url = f"{BASE_URL}/categories/{category_id}"
            resp = requester.get(url, {})["data"]
            name = "_".join(resp["name"].split())
            self.id_to_name[category_id] = name
        sub_categories = self._get_sub_categories(category_id, requester)
        for variable, value in run["values"].items():
            sub_name = "_".join(
                sub_categories.get(variable, {}).get(value, "").split("_")
            )
            if sub_name:
                name = f"{name}_{sub_name}"
        return name

    def fetch_player(self, player_id, requester):
        if player_id in self.id_to_name:
            return self.id_to_name[player_id]
        url = f"{BASE_URL}/users/{player_id}"
        resp = requester.get(url, {})
        if resp.get("status") == 404:
            return player_id
        resp = resp["data"]
        name = "_".join(resp["names"]["international"].split())
        self.id_to_name[player_id] = name
        return name


class RunsFilter:
    """Configurable filter for indexed runs."""

    def __init__(self, game_id, category_id, highlights_only, current_only, keep_rank):
        self.game_id = game_id
        self.category_id = category_id
        self.highlights_only = highlights_only
        self.current_only = current_only
        self.keep_rank = keep_rank
        self.run_rank_at_submit = {}

    @staticmethod
    def _filter_run_type(
        game_id,
        category_id,
        all_run_data,
    ):
        filtered_run_data = {}
        for run_id, run in all_run_data.items():
            if run["game_id"] == game_id and (
                category_id is None or run["category_id"] == category_id
            ):
                filtered_run_data[run_id] = run
        return filtered_run_data

    @staticmethod
    def _filter_current(all_run_data):
        # Sort runs by player in each category, and keep the player's best.
        runs_by_type = defaultdict(list)
        for run in all_run_data.values():
            run_type_id = (run["game"], run["category"], run["player_ids"])
            runs_by_type[run_type_id].append(run)
        all_run_data = {}
        for runs in runs_by_type.values():
            runs = sorted(runs, key=lambda run: run["time"])
            best_run = runs[0]
            all_run_data[best_run["id"]] = best_run
        return all_run_data

    def _filter_rank(self, all_run_data):
        filtered_run_data = {}
        runs_by_category = defaultdict(list)
        for run in all_run_data.values():
            runs_by_category[run["category"]].append(run)
        for category_runs in runs_by_category.values():
            # If we've already restricted to current runs, just keep the requested ranks.
            if self.current_only:
                by_run_time = sorted(category_runs, key=lambda x: x["time"])
                for index, run in enumerate(by_run_time[: self.keep_rank]):
                    self.run_rank_at_submit[run["id"]] = index
                    filtered_run_data[run["id"]] = run
                continue

            rank_by_runner = {}
            by_run_date = sorted(
                [(datetime.fromisoformat(run["date"]), run) for run in category_runs],
                key=lambda x: x[0],
            )

            current_runs = []
            for _, run in by_run_date:
                run_with_time = (run["time"], run)
                run_rank = bisect(current_runs, run_with_time[0], key=lambda x: x[0])
                # Less than here because we need to be strictly below the limit.
                if run_rank < self.keep_rank:
                    # If the player already has a run on the board,
                    if run["player_ids"] not in rank_by_runner:
                        current_runs.insert(run_rank, run_with_time)
                        filtered_run_data[run["id"]] = run
                        rank_by_runner[run["player_ids"]] = run_rank
                        self.run_rank_at_submit[run["id"]] = run_rank
                    else:
                        # Less or equal here because the new PB might not have changed the player's rank.
                        if run_rank <= rank_by_runner[run["player_ids"]]:
                            del current_runs[rank_by_runner[run["player_ids"]]]
                            current_runs.insert(run_rank, run_with_time)
                            rank_by_runner[run["player_ids"]] = run_rank
                            filtered_run_data[run["id"]] = run
                            self.run_rank_at_submit[run["id"]] = run_rank
        return filtered_run_data

    @staticmethod
    def _filter_highlights(all_run_data):
        # Keep runs with only twitch links.
        filtered_run_data = {}
        for run in all_run_data.values():
            _, all_twitch = uses_twitch(run["links"])
            if all_twitch:
                filtered_run_data[run["id"]] = run
        return filtered_run_data

    def filter(self, all_run_data):
        # Filter the index down to the requested set.
        # Current needs to come first, since we could have an old highlight and a current youtube video.
        all_run_data = self._filter_run_type(
            self.game_id, self.category_id, all_run_data
        )
        if self.current_only:
            all_run_data = self._filter_current(all_run_data)
        if self.keep_rank is not None:
            all_run_data = self._filter_rank(all_run_data)
        if self.highlights_only:
            all_run_data = self._filter_highlights(all_run_data)
        return all_run_data


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-g",
        "--game-id",
        help="The id of the game.",
    )
    parser.add_argument(
        "-c",
        "--category-id",
        help="The id of the category.",
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List all categories for a game.",
    )
    parser.add_argument(
        "--lookup-game",
        help="Lookup the id for a game.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download videos for a game or category.",
    )
    parser.add_argument(
        "--run-index",
        default=".",
        help="The path to load and write the run index from/to.",
    )
    parser.add_argument(
        "--highlights-only",
        action="store_true",
        default=False,
        help="Only download twitch highlights.",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        default=False,
        help="Download only current runs.",
    )
    parser.add_argument(
        "--keep-better",
        type=int,
        help="Only archive runs this rank or better at the time they were submit. "
        "If --current-only is used then keep current runs at or above this rank.",
    )
    parser.add_argument(
        "--downgrade-quality",
        type=int,
        help="Like --keep-better, except runs worse than this rank will be saved at low quality. "
        "Note that this depends on the video hosting site having multiple quality options available.",
    )
    parser.add_argument(
        "--list-missing",
        action="store_true",
        help="Print an SRC link for any run such that we do not have a stored video.",
    )
    parser.add_argument(
        "-q",
        "--quality",
        help="Set quality parameters for video download. The format is <format;format sort> "
        "See https://github.com/yt-dlp/yt-dlp?tab=readme-ov-file#format-selection",
    )
    parser.add_argument(
        "--low-quality",
        default="b;+res:360,fps",
        help="Set low quality parameters for video download. The format is <format;format sort> "
        "See https://github.com/yt-dlp/yt-dlp?tab=readme-ov-file#format-selection",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        exit(0)
    return parser.parse_args()


def main():
    args = get_args()
    requester = RateLimitedRequest()
    src_names = SRCIDsToNames()
    # Always set keep better, so that the rankings will get populated.
    # The downgrade quality option needs the ranks, and setting keep better
    # to an arbitrary high value has no impact on the results.
    runs_filter = RunsFilter(
        args.game_id,
        args.category_id,
        args.highlights_only,
        args.current_only,
        args.keep_better or 999999999999,
    )
    if args.list_categories:
        display_categories_for_user(args.game_id, requester)
    elif args.lookup_game:
        lookup_game_id_for_user(args.lookup_game, requester)
    elif args.download:
        download_videos_for_user(
            args.run_index,
            args.game_id,
            requester,
            src_names,
            runs_filter,
            args.low_quality,
            args.downgrade_quality,
            args.category_id,
            args.quality,
        )
    elif args.list_missing:
        find_missing_for_user(
            args.run_index,
            args.game_id,
            requester,
            src_names,
            runs_filter,
            args.category_id,
        )


def display_categories_for_user(game_id, requester: RateLimitedRequest):
    categories = get_all_categories(game_id, requester)
    for category in categories:
        print(f"{category['name']}: {category['id']}")
        print(f"      {category['weblink']}")


def lookup_game_id_for_user(search_string, requester: RateLimitedRequest):
    url = f"{BASE_URL}/games"
    query = {"name": search_string}
    games = requester.get(url, query)["data"]
    for game in games:
        print(f"{game['names']['international']}: {game['id']}")


def prepare_local_index(
    game_id, category_id, runs_index_location, src_names, requester, runs_filter
):
    # Collect all the related runs from SRC and update the runs index.
    if category_id is None:
        runs = get_all_runs_for_game(game_id, requester)
    else:
        runs = get_all_runs_for_category(game_id, category_id, requester)
    all_run_data = log_all_runs(runs, runs_index_location, src_names, requester)

    # Filter the index down to the requested set.
    all_run_data = runs_filter.filter(all_run_data)

    # Check which runs we already have.
    makedirs(f"{runs_index_location}/{RUNS_FOLDER}", exist_ok=True)
    for dir_member in listdir(f"{runs_index_location}/{RUNS_FOLDER}"):
        if isfile(f"{runs_index_location}/{RUNS_FOLDER}/{dir_member}"):
            extension_index = dir_member.rfind(".")
            run_id_start = dir_member.rfind("-") + 1
            archived_run_id = dir_member[run_id_start:extension_index]
            if archived_run_id in all_run_data:
                all_run_data[archived_run_id]["stored_locally"] = True
    return all_run_data


def download_videos_for_user(
    runs_index_location,
    game_id,
    requester: RateLimitedRequest,
    src_names: SRCIDsToNames,
    runs_filter: RunsFilter,
    low_quality,
    quality_downgrade_rank=None,
    category_id=None,
    quality=None,
):
    # Collect all the related runs from SRC and update the runs index.
    all_run_data = prepare_local_index(
        game_id,
        category_id,
        runs_index_location,
        src_names,
        requester,
        runs_filter,
    )

    # Attempt to fetch the rest.
    for run_id, run_data in all_run_data.items():
        if not run_data.get("stored_locally"):
            print(f"Downloading: {run_data['weblink']}")

            quality_for_download = quality
            run_rank = runs_filter.run_rank_at_submit[run_id]
            if quality_downgrade_rank and run_rank >= quality_downgrade_rank:
                quality_for_download = low_quality
            for video_url in run_data["links"]:
                try:
                    download_video(
                        video_url,
                        runs_index_location,
                        run_data,
                        src_names,
                        requester,
                        quality_for_download,
                    )
                    run_data["stored_locally"] = True
                    break
                except VideoDownloadException as ex:
                    print(ex)
            if not run_data.get("stored_locally"):
                print(f"Failed to download: {run_data['weblink']}")


def get_human_readable_name(
    run, src_names: SRCIDsToNames, requester: RateLimitedRequest
):
    game = run["game"]
    category = run["category"]
    run_time = run["time"]
    run_id = run["id"]
    run_date = run["date"]

    player_ids = run["player_ids"]
    players = "-".join(
        [src_names.fetch_player(player_id, requester) for player_id in player_ids]
    )
    return f"{game}-{category}-{run_time}-{run_date}-{players}-{run_id}"


def find_missing_for_user(
    runs_index_location,
    game_id,
    requester: RateLimitedRequest,
    src_names: SRCIDsToNames,
    runs_filter: RunsFilter,
    category_id=None,
):
    # Collect all the related runs from SRC and update the runs index.
    all_run_data = prepare_local_index(
        game_id,
        category_id,
        runs_index_location,
        src_names,
        requester,
        runs_filter,
    )

    total_missing = 0
    for run_data in all_run_data.values():
        if not run_data.get("stored_locally"):
            print(run_data["weblink"])
            total_missing += 1
    print(f"There are {total_missing} missing runs.")


def filter_live(info):
    if info.get("is_live", False):
        return "Skipping live stream"
    return None


def download_video(
    video_url,
    runs_index_location,
    run,
    src_names: SRCIDsToNames,
    requester: RateLimitedRequest,
    quality=None,
):
    file_name = get_human_readable_name(run, src_names, requester)

    ydl_options = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": f"{runs_index_location}/{RUNS_FOLDER}/{file_name}.%(ext)s",
        "noplaylist": True,
        "match_filter": filter_live,
        "verbose": False,
        "quiet": True,
        "sleep-interval": 5,
        "retries": 1,
    }
    if quality:
        if len(quality.split(";")) == 1:
            ydl_options["format"] = quality
        elif len(quality.split(";")) == 2:
            format_str, sort_str = quality.split(";")
            ydl_options["format"] = format_str
            ydl_options["format_sort"] = [sort_str]
        else:
            raise ValueError(f"Cannot part quality string: {quality}")
    try:
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            ydl.download([video_url])
    except Exception as ex:
        raise VideoDownloadException(f"Failed to download: {video_url}") from ex


def uses_twitch(links):
    uses_twitch = ["twitch" in link for link in links]
    return any(uses_twitch), all(uses_twitch)


def log_all_runs(
    runs, runs_index_location, src_names: SRCIDsToNames, requester: RateLimitedRequest
):
    path = f"{runs_index_location}/{INDEX_FILE}"
    if isfile(path):
        with open(path, "r") as inF:
            all_run_data = json.loads(inF.read())
    else:
        all_run_data = {}

    for run_data in all_run_data.values():
        # We want to use these as dict keys, but json deserializes them as a list.
        run_data["player_ids"] = tuple(run_data["player_ids"])

    for run in runs:
        videos = run.get("videos")
        if videos is None:
            continue
        if "links" in videos:
            game = src_names.fetch_game(run["game"], requester)
            category = src_names.fetch_category(run, requester)
            player_ids = tuple(
                sorted(
                    [
                        player.get("id", player.get("name", ""))
                        for player in run["players"]
                    ]
                )
            )
            links = set(link["uri"] for link in videos.get("links"))
            all_run_data[run["id"]] = {
                "id": run["id"],
                "date": run.get("date") or run.get("submitted") or "1000-01-01",
                "player_ids": player_ids,
                "game": game,
                "game_id": run["game"],
                "category": category,
                "category_id": run["category"],
                "time": float(run["times"]["primary_t"]),
                "links": list(links),
                "weblink": run["weblink"],
            }

        # Lazy way to do it, but saves partial progress for indexing.
        with open(path, "w") as outF:
            outF.write(json.dumps(all_run_data))

    return all_run_data


def get_all_categories(game_id, requester: RateLimitedRequest):
    url = f"{BASE_URL}/games/{game_id}/categories"
    return requester.get(url)["data"]


def get_all_runs_for_category(game_id, category_id, requester: RateLimitedRequest):
    url = f"{BASE_URL}/runs"
    query = {
        "max": MAX_ELEMENTS,
        "category": category_id,
        "orderby": "submitted",
        "game": game_id,
        "direction": "asc",
    }
    while True:
        resp = requester.get(url, query)
        runs = resp["data"]
        for run in runs:
            videos = run.get("videos")
            if videos is None:
                continue
            if run["status"]["status"] != "verified":
                continue
            yield run
        next_url = [
            link for link in resp["pagination"]["links"] if link["rel"] == "next"
        ]
        if next_url:
            url = next_url[0]["uri"]
        else:
            break


def get_all_runs_for_game(game_id, requester: RateLimitedRequest):
    category_ids = [
        category["id"] for category in get_all_categories(game_id, requester)
    ]
    for category_id in category_ids:
        for run in get_all_runs_for_category(game_id, category_id, requester):
            yield run


if __name__ == "__main__":
    main()
