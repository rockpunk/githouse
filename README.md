# githouse

A simple cli to get a report of Github PRs and Clubhouse stories that team members
have merged in the past week.

## Disclaimers

This was a quick hack. It's not pretty, and it likely has bugs and is not user friendly. I'm only throwing
it up here in case others at my org find it useful for tracking recent team contributions.

## Requirements

- A clubhouse API token
- A github API token
- A github team
- A github organization

These can all be set in environment variables:

- `CH_TOKEN`
- `GH_TOKEN`
- `GH_TEAM`
- `GH_ORG`

## Installation

Can run directly in virtualenv w/

    $ pip install poetry
    $ poetry install
    $ poetry run githouse -h

Or, if you prefer to install locally:

    $ poetry build
    $ pip install dist/githouse-0.1.0-py3-none-any.whl
    $ githouse -h

## Usage

### List the github members of a team
```
    $ githouse users --gh-team my-team

    Team Members in @my-team
        rockpunk
        user1
        user2
```

### Generate a markdown report

**NOTE**: This can take a while for a huge number of reports

```
    $ githouse report --gh-team my-team | tee weekly_report.md

    # My Team Updates 2021-06-01 - 2021-06-07

    ## user1 (3 Stories; 3 PRs)
     * [Bug ch23](https://app.clubhouse.io/story/23): fix column names that are wrapped in one column
       * [#my-repo/1485](https://github.com/rockpunk/my-reopo/pull/1485): fix bad columns
     * [Bug ch26](https://app.clubhouse.io/story/26): fix daily import job

    ## user2 (0 Stories; 0 PRs)

```

The generated md is perfect for sharing in a gist. The [github cli](https://cli.github.com) is awesome, btw:

```
    $ githouse report --gh-team my-team | gh gist create -f my_team_updates.md -
    - Creating gist my_team_updates.md
    ✓ Created gist my_team_updates.md
    https://gist.github.com/<your_gist>
```

### Store a markdown report json for further manip

```
    $ githouse report --gh-team my-team -o some_report.json
    $ cat some_report.json | jq '[.report | .[] | .total_prs] | add'
    24
```

### Reprint a report from a previous report json file

This is useful if you want to see the report output without hitting all the webservices
again.

```
    $ githouse report -f some_report.json

    # My Team Updates 2021-06-01 - 2021-06-07
    ....
```
