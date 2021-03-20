gitlab-timesheet
================

Generate timesheet CSVs from GitLab time spent entries.
This way you can do complete time tracking using GitLab's `/spend` commands.

[Time Tracking in GitLab](https://docs.gitlab.com/ee/user/project/time_tracking.html)


Installation
------------

Requires Python 3.7 or later.

    pip install python-gitlab


Usage
-----

    python timesheet.py --token GITLAB_API_TOKEN --projects 123456

Replace `GITLAB_API_TOKEN` with your [GitLab personal access token](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html).
Make sure the token has the `read_api` permission.
To query a self-hosted GitLab instance, add `--url https://git.example.com` with the custom URL.

The script queries time spends from all project ids given with `--projects`, separated with a `/`.
The default is to query and sum spends in all accessible projects.

The time frame can be changed using the `--start` and `--end` arguments,
which default to the first day of this month and today, respectively.

More options for filtering are available, check `./timesheet.py --help`.


Output
------

The script creates a CSV file per user in the current folder with the following structure:

| Date       | USERNAME | Start | End   | Issues                                              |
| ---------- | -------- | ----- | ----- | --------------------------------------------------- |
| 2021-01-01 | 8h00m    | 10:00 | 18:00 | Project#1 Story Title: 480 min                      |
| 2021-01-02 | 4h00m    | 10:00 | 14:00 | Project#2 Title2: 180 min; Project#3 Title3: 60 min |
