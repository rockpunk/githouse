import click
import collections
import datetime as dt
import dateutil.relativedelta as rd
import dateutil.parser
import github
import json
import logging
import pathlib
import re
import requests

logging.basicConfig()
logger = logging.getLogger()


class Options:
    gh_token = None
    gh_team = None
    gh_org = None
    ch_token = None

    def __str__(self):
        return str(self.__dict__)

    @staticmethod
    def set_thing(name):
        def setter(ctx, _, value):
            opts = ctx.ensure_object(Options)
            if value:
                if hasattr(opts, f"set_{name}"):
                    return getattr(opts, f"set_{name}")(value, ctx=ctx)
                else:
                    setattr(opts, name, value)
            else:
                if not hasattr(opts, name):
                    setattr(opts, name, value)
            return value

        return setter

    @staticmethod
    def gh_org_opt():
        return click.option(
            "--gh-org",
            help="The github organization to use",
            envvar="GH_ORG",
            show_envvar=True,
            callback=Options.set_thing("gh_org"),
        )

    @staticmethod
    def gh_token_opt():
        return click.option(
            "--gh-token",
            help="The github access token to use",
            envvar="GH_TOKEN",
            show_envvar=True,
            callback=Options.set_thing("gh_token"),
        )

    @staticmethod
    def gh_team_opt():
        return click.option(
            "--gh-team",
            help="The github team to use",
            envvar="GH_TEAM",
            show_envvar=True,
            callback=Options.set_thing("gh_team"),
        )

    @staticmethod
    def ch_token_opt():
        return click.option(
            "--ch-token",
            help="The github access token to use",
            envvar="CH_TOKEN",
            show_envvar=True,
            callback=Options.set_thing("ch_token"),
        )

    @staticmethod
    def verbose_opt():
        return click.option(
            "-v",
            "--verbose",
            help="Be verbose. More v's, more verbose.",
            count=True,
            callback=Options.set_thing("verbose"),
        )

    def set_verbose(self, val, **kwds):
        self.verbose = val
        lev = "WARNING"
        if val > 1:
            lev = "DEBUG"
        else:
            lev = "INFO"
        logger.setLevel(lev)
        return self.verbose


pass_opts = click.make_pass_decorator(Options, ensure=True)


@click.group("manager", context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option()
@click.pass_context
def cli(ctx, **kwds):
    """
    A tool to follow clubhouse tickets
    """
    pass


@cli.command()
@Options.gh_token_opt()
@Options.gh_team_opt()
@Options.gh_org_opt()
@Options.verbose_opt()
@pass_opts
@click.pass_context
def users(ctx, opts, *args, **kwds):
    gh, team = init_gh(opts)
    click.secho(f"Team Members in @{team.name}", bold=True)
    for m in sorted(team.get_members(), key=lambda x: x.login):
        click.secho(f"\t{m.login}", fg="blue")


@cli.command()
@Options.gh_org_opt()
@Options.gh_token_opt()
@Options.gh_team_opt()
@Options.verbose_opt()
@Options.ch_token_opt()
# @click.option('-u', '--user', multiple=True, help='The user to limit by.')
# @click.option('-p', '--project', help='An optional project to limit by')
@click.option(
    "-s",
    "--state",
    help="The state the PR should be in.",
    type=click.Choice(["merged", "closed", "updated", "created"]),
    default="merged",
)
@click.option(
    "--start-date",
    metavar="YYYY-MM-DD",
    help="Start of date range for PRs to consider. Defaults to Monday of this week.",
)
@click.option(
    "--end-date",
    metavar="YYYY-MM-DD",
    help="End of date range for PRs to consider. Defaults to yesterday.",
)
@click.option(
    "-o", "--outfile", help="Save json report data to OUTFILE", type=click.Path()
)
@click.option(
    "-f",
    "--filename",
    help="A file to read an already pulled report from. This avoids hitting github and clubhouse again",
    type=click.Path(exists=True),
)
@pass_opts
@click.pass_context
def report(ctx, opts, start_date, end_date, state, filename, outfile, *args, **kwds):
    """
    List stories completed in last week.

    This will find all PRs that have been merged in the date range specified.
    Please keep in mind that a long date range (or a large # of PRs) will cause
    a large amount of network IO to and from github and clubhouse APIs. This will
    hit github once for each 30 PRs returned for search, once to get the team members,
    as well as once per each PR.

    So, if you have a 100PRs merged in a daterange, this script will hit the Github API
    105 times, and the clubhouse API 100 times. (This is unfortunately necessary to get
    the branch name for each PR, and story metadata from clubhouse.) A github access
    token should allow for up to 5000 requests / hour, and Clubhouse allows up to
    200 requests per minute. You _should_ be fine. I use this regularly for a team of 12
    people, with ~100 PRs in a busy week.

    If for some reason you do get throttled, use a smaller date range (or a less
    productive team! ;) )
    """

    if not filename:
        if not opts.ch_token:
            raise RuntimeError("A clubhouse token is required.")

        gh, team = init_gh(opts)
        members = [m.login for m in team.get_members()]
        author_query = " ".join([f"author:{m}" for m in members])

        today = dt.datetime.now()
        if not start_date:
            if today.weekday() == 0:
                start_date = today - rd.relativedelta(days=7)
            else:
                start_date = today - rd.relativedelta(days=today.weekday())
            start_date = start_date.strftime("%Y-%m-%d")
        if not end_date:
            end_date = (today - rd.relativedelta(days=1)).strftime("%Y-%m-%d")

        q = f"type:pr org:{opts.gh_org} {state}:{start_date}..{end_date} {author_query}"
        logger.info("Github PR query: %s", q)

        # work around pygithub's request-happy arch + bug not supporting repeated qualifiers
        returned_prs = [pr for pr in hit_gh(opts, "/search/issues", data={"q": q})]

        logger.warning("Total of %s PRs returned", len(returned_prs))

        last_user = ""
        user_report = collections.defaultdict(lambda: [])

        report = empty_report()

        # 'user': { 'stories': {'123': { 'id':ch123, 'name': 'fix stuff', 'type':'bug', prs=[] }, ... }, 'misc_prs': [ ... ], 'total_prs':4, 'total_stories':2 }
        for pr in sorted(returned_prs, key=lambda x: rget(x, "user.login")):
            author = rget(pr, "user.login")
            if author != last_user:
                if last_user:
                    logger.debug("Storing report for %s", last_user)
                    report["total_stories"] = len(report["stories"])
                    report["total_prs"] = sum(
                        [len(v["prs"]) for k, v in report["stories"].items()]
                    ) + len(report["misc_prs"])

                    user_report[last_user] = report
                    report = empty_report()
                logger.debug("Processing PRs for %s", author)
                last_user = author

            pr_uri = rget(pr, "pull_request.url")
            assert pr_uri, "Each PR should have a URL"

            branch = ""
            for pr_obj in hit_gh(opts, uri=pr_uri):
                logger.debug(pr_obj)
                branch = rget(pr_obj, "head.ref")
                logger.info("PR Branch = %s", branch)

            assert pr_obj, "PR object should be returned"
            assert branch, "Each PR should have a branch"

            m = story_re.search(branch)
            if m:
                story_id = m.group("story_id").strip("ch")
                story = hit_ch(opts, api=f"stories/{story_id}")

                if story_id not in report["stories"]:
                    report["stories"][story_id].update(story)
                report["stories"][story_id]["prs"].append(pr_obj)
            else:
                report["misc_prs"].append(pr_obj)

        # store the last user's report
        report["total_stories"] = len(report["stories"])
        report["total_prs"] = sum(
            [len(v["prs"]) for k, v in report["stories"].items()]
        ) + len(report["misc_prs"])
        user_report[last_user] = report

        if outfile:
            with open(outfile, "w") as f:
                dump = {"members": members, "report": user_report}
                f.write(json.dumps(dump))
            logger.warning("Full report saved to %s", outfile)
    else:
        with open(filename, "r") as f:
            report = json.load(f)
            user_report = report["report"]
            members = report["members"]

    pretty_team = re.sub(r"[_-]+", " ", opts.gh_team).title()

    click.secho(f"# {pretty_team} Updates {start_date} - {end_date}")

    for author in sorted(members):
        report = user_report.get(author, empty_report())
        ies = "ies" if report["total_stories"] != 1 else "y"
        s = "s" if report["total_prs"] != 1 else ""

        click.secho(
            f"\n## {author} ({report['total_stories']} Stor{ies}; {report['total_prs']} PR{s})",
            bold=True,
        )
        for story_id, story in report["stories"].items():
            title = f"[{story['story_type'].capitalize()} ch{story_id}]({story['app_url']}): {story['name']}"
            click.secho(f" * {title}", fg="green")

            for pr in story["prs"]:
                repo = rget(pr, "head.repo.name", "")
                title = f"   * [#{repo}/{pr.get('number')}]({pr.get('html_url')}): {pr.get('title')}"
                click.secho(f"{title}", fg="blue")

        if len(report["misc_prs"]):
            click.secho(f" * Misc PRs", fg="green")
            for pr in report["misc_prs"]:
                repo = rget(pr, "head.repo.name", "")
                title = f"   * [#{repo}/{pr.get('number')}]({pr.get('html_url')}): {pr.get('title')}"
                click.secho(f"{title}", fg="blue")


story_re = re.compile(r"(?P<story_id>\bch\d+\b)")
link_re = re.compile(r'<(?P<link>[^>]+)> *; *rel= *"(?P<relationship>[^"]+)"')


def empty_report():
    return {
        "stories": collections.defaultdict(lambda: {"prs": []}),
        "misc_prs": [],
        "total_prs": 0,
        "total_stories": 0,
    }


def rget(d, path, default=None):
    if isinstance(path, (str)):
        paths = path.split(".")
    else:
        paths = path
    subd = d.get(paths[0], {})
    if not subd:
        return default
    elif len(paths) == 1:
        return subd
    else:
        return rget(subd, paths[1:], default)


def hit_ch(opts, api=None, uri=None, data={}, method="GET"):
    assert api or uri, "Either an api or a URI should be passed"

    if api:
        api = api.strip("/")
    if not uri:
        uri = f"https://api.clubhouse.io/api/v3/{api}"

    logger.info("Hitting clubhouse %s", uri)

    # TODO: this is ugly
    if method == "GET":
        params = data
        data = None
    if method == "POST":
        params = None
        data = data

    resp = requests.request(
        method,
        uri,
        params=params,
        data=data,
        headers={"Clubhouse-Token": opts.ch_token},
    )
    logger.info("Clubhouse response: %s", resp)
    resp.raise_for_status()

    return resp.json()


def hit_gh(opts, api=None, uri=None, data={}, method="GET"):
    """
    hack around pygithub's request-happy architecture

    NOTE: this will return an iterator even if there's only
    one json object response.
    """
    assert api or uri, "Either an api or a uri should be passed"

    if api:
        api = api.strip("/")
    if not uri:
        uri = f"https://api.github.com/{api}"

    logger.info("Hitting github %s", uri)

    # TODO: this is ugly
    if method == "GET":
        params = data
        data = None
    if method == "POST":
        params = None
        data = data

    resp = requests.request(
        method,
        uri,
        params=params,
        data=data,
        headers={"Authorization": f"Token {opts.gh_token}"},
    )
    logger.info("Github response: %s", resp)
    resp.raise_for_status()

    res = resp.json()

    if res.get("items", []):
        links = {}
        logger.info("Links %s", resp.headers.get("Link"))

        for uri, rel in link_re.findall(resp.headers.get("Link", "")):
            logger.debug("Links %s -> %s", rel, uri)
            links[rel] = uri

        items = resp.json().get("items", [])
        logger.info("Got %s PRs", len(items))

        for item in items:
            # logger.debug("Yielding %s", item)
            yield item

        if method == "GET" and links.get("next"):
            for item in hit_gh(
                opts, api, uri=links.get("next"), data=data, method=method
            ):
                yield item
    else:
        yield res


def init_gh(opts):
    """
    Return a tuple of a github client, and a Team object
    """
    if not opts.gh_token:
        raise RuntimeError(
            "A github token is required. Use --gh-token option or GH_TOKEN env var"
        )
    if not opts.gh_team:
        raise RuntimeError(
            "A github team is required. Use the --gh-team option or GH_TEAM env var"
        )
    if not opts.gh_org:
        raise RuntimeError(
            "A github org is required. Use the --gh-org option or GH_ORG env var"
        )

    logger.info(f"Initing GitHub w/ {opts}")
    gh = github.Github(opts.gh_token)
    logger.info("Getting team...")
    team = gh.get_organization(opts.gh_org).get_team_by_slug(opts.gh_team)
    logger.info("Got team: %s", team)
    return gh, team
