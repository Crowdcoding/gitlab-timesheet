#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from __future__ import annotations

"""
timesheet.py

CLI based script to read all time spents of a GitLab instance via the GitLAb API and
print the gathered data foreach found user to a CSV file.
"""

__author__ = "Crowdcoding IT Solutions UG (haftungsbeschränkt)"
__credits__ = ["Carsten Docktor"]
__license__ = "MPL"
__version__ = "1.0"
__maintainer__ = "Crowdcoding IT Solutions UG (haftungsbeschränkt)"
__email__ = "info@crowdcoding.it"
__status__ = "Production"

import sys
import time
import argparse
import logging
import datetime
import os
import re
import requests
try:
	import gitlab
	import gitlab.v4.objects
except ModuleNotFoundError as e:
	e.msg = "No module named 'python-gitlab'"
	raise e
import concurrent.futures
import csv
from typing import List, Dict, Iterator, Union
import math


start_time = time.time()

def date_range(date1: datetime.date, date2: datetime.date) -> Iterator[datetime.date]:
	"""Return a generator of dates between date1 and inclusively date2."""
	for n in range(int((date2 - date1).days) + 1):
		yield date1 + datetime.timedelta(n)


def print_time_of_day(date: datetime.datetime) -> str:
	"""Format a date.datetime object to be printed in the format 10:00."""
	return "{:%H:%M}".format(date)


def round_up(f: float, d: int) -> float:
	"""
	Round up a float after d digits.

	Example: round_up(1.654, 2) -> 1.66
	"""
	return math.ceil(f * 10**d) / 10**d


def translate_tex(s: str):
	"""Translate special characters for Tex usage."""
	return s.translate(str.maketrans({"\\": r"\\",
	                                  "]": r"{\]}",
	                                  "^": r"{\^}",
	                                  "#": r"{\#}",
	                                  "$": r"{\$}",
	                                  "*": r"{\*}",
	                                  ",": r".",
	                                  "&": r"{\&}"}))


IssueOrMergeRequest = Union[gitlab.v4.objects.ProjectIssue, gitlab.v4.objects.ProjectMergeRequest]

class Note:
	"""
	This class holds all important information of a time spent regarding the timesheet creation.

	Not to be confused with ProjectMergeRequestNote resp. ProjectIssueNote from python gitlab.

	Multiple time spents in the same issue or merge request on the same day will be merged together
	by adding the time spent and the corresponding date to the list.

	:type issue_mr: Issue or MergeRequest
	:param time_spent: Spent time in minutes

	"""
	def __init__(self, project: gitlab.v4.objects.Project, issue_mr: IssueOrMergeRequest, user: dict,
				 time_spent: int, spend_date: datetime.date, spend_datetime: datetime.datetime=None):
		# Token used by GitLab between the project_id and the issue_id
		# Example: 8#214 for issue number 214 in project 8
		if isinstance(issue_mr, gitlab.v4.objects.ProjectIssue):
			self.token = "#"
		elif isinstance(issue_mr, gitlab.v4.objects.ProjectMergeRequest):
			self.token = "!"
		else:
			raise NotImplementedError

		self.project = project
		self.issue_mr = issue_mr
		self.user = user
		self.time_spents = [time_spent]
		self.spend_date = spend_date
		self.spend_datetimes = [spend_datetime]

	def time_spent(self) -> int:
		return sum(self.time_spents)

	def title(self) -> str:
		# issue_mr.id is the unique id over all issues/merge requests.
		# Here, we need the iid which is project related.
		return f"{self.project.name}{self.token}{self.issue_mr.iid} {self.issue_mr.title}: {self.time_spent()} min"

	def __eq__(self, other: Note) -> bool:
		"""Compare issue/merge request id (which is unique under all issues), user id and spend date."""
		if isinstance(other, Note):
			return self.issue_mr.id == other.issue_mr.id and \
			       self.user["id"] == other.user["id"] and \
			       self.spend_date == other.spend_date
		else:
			raise NotImplementedError

	def __lt__(self, other: Note) -> bool:
		"""Order by project id, issue/merge request id and spend date lexicographically."""
		if isinstance(other, Note):
			return (self.project.id, self.issue_mr.iid, self.spend_date)\
			       < (other.project.id, other.issue_mr.iid, other.spend_date)
		else:
			raise NotImplementedError

	def __repr__(self):
		return f"<{self.__class__.__name__}'iid:{self.issue_mr.iid}> => {self.__dict__}"

	def add(self, other: Note) -> None:
		"""Merge two __eq__ time spents by adding new other time spent and spend time to the list."""
		self.time_spents.extend(other.time_spents)
		self.spend_datetimes.extend(other.spend_datetimes)


class IssueAndMrList:
	"""
	Composition of a list object which holds Notes.
	Adding functionality to work with the Notes directly.
	"""
	def __init__(self):
		self.notes = []

	def projects(self) -> Dict[int, str]:
		return {note.project.id: note.project.name for note in self.notes}

	def users(self) -> Dict[int, str]:
		return {note.user["id"]: note.user["username"] for note in self.notes}

	def __repr__(self) -> str:
		return "\n".join(map(str, self.notes))

	def update(self, note: Note) -> None:
		"""
		If the note already exists in the list, add the new information to it,
		otherwise, append the new note to the list.
		"""
		if note in self.notes:
			i = self.notes.index(note)
			self.notes[i].add(note)
		else:
			self.notes.append(note)

	def sum_project(self, project_id: int) -> int:
		"""Return sum of all time spents in a specific project."""
		return sum(i.time_spent() for i in self.notes if project_id == i.project.id)

	def sum_user(self, user_id: int) -> int:
		"""Return sum of all time spents for a specific user."""
		return sum(i.time_spent() for i in self.notes if user_id == i.user["id"])

	def get_date_user(self, date: datetime.date, user_id: int) -> Iterator[Note]:
		"""Return Iterator over all notes of the same date and user."""
		return filter(lambda i: date == i.spend_date and user_id == i.user["id"], self.notes)

	def sum_date_user(self, date: datetime.date, user_id: int) -> int:
		"""Return sum of all time spents of a specific date from a user."""
		return sum(i.time_spent() for i in self.get_date_user(date, user_id))

	def add_break_time(self, worked_minutes: int, date: datetime.date, user_id: int) -> int:
		"""

		:param worked_minutes: Number of minutes a person worked at a day
		:return: Number of minutes the person attended including legal German break times
		"""
		# legal break times for Germany
		#	at least 30 min after 6 Hours
		# 	at least 45 min after 9 Hours
		#	In general, working more than 10 hours is forbidden
		if worked_minutes <= 6 * 60:
			return worked_minutes
		elif worked_minutes <= 9 * 60:
			return worked_minutes + 30
		elif worked_minutes <= 10 * 60:
			return worked_minutes + 45
		else:
			logging.warning(f"User {self.users()[user_id]} worked more than 10 hours on {date}.")
			return worked_minutes + 45

	def get_user_row(self, date: datetime.date, user_id: int, extern_version: bool=False) -> List[str]:
		"""Return a row for the time sheet in list format to be used by the csv writer."""
		# TODO Algorithm could be faster if we sort the list
		#      and do the creation of the whole timesheet in the class method
		# Needed change:
		#  - do not use Data.update, append new notes instead,
		#  - sort afterwards,
		#  - clean entries with time_spent == 0,
		#  - process for csv
		notes = list(self.get_date_user(date, user_id))

		if not notes:
			# Return empty row where only the date is set.
			# We need empty strings to avoid bugs in CSV/Tex.
			return [date] + 4*[""]

		# spent times are always in minutes
		worked_minutes = sum(i.time_spent() for i in notes)

		# TODO calculate start and end time of day better
		# use spend_datetimes and time_spents for this
		# merge notes for same day..
		# check if breaking times are necessary
		start_time = datetime.datetime(1970, 1, 1, 10)  # start time 10:00

		#add break time only for official version
		if extern_version:
			attended_minutes = self.add_break_time(worked_minutes, date, user_id)
		else:
			attended_minutes = worked_minutes

		end_time = start_time + datetime.timedelta(minutes=attended_minutes)

		titles = "; ".join([(translate_tex(i.title()) if args.tex else i.title()) for i in notes if i.time_spent() != 0])


		return [date,
		        f"{worked_minutes // 60}h{worked_minutes % 60:02}m" if worked_minutes != 0 else "",
		        print_time_of_day(start_time),
		        print_time_of_day(end_time),
		        f"{attended_minutes-worked_minutes}m" if extern_version else titles]

	def print_csv_users(self, extern_version: bool=False) -> None:
		"""
		Print time spents of a user to a CSV file.

		Header: Date | USERNAME | Start | End | Pause or Issues

		Body: date | time spent in min | start time | end time | issue titles with time spents

		Footer: Sum Min
				Sum h
		"""
		for user_id, username in self.users().items():
			time_spent = self.sum_user(user_id)
			try:
				with open("timesheet-" + username + ("-official" if extern_version else "")
				          + ".csv", "w", newline="", encoding="utf8") as csvfile:
					wr = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)

					wr.writerow(["Date", username, "Start", "End", ("Pause" if extern_version else "Issues")])
					for date in date_range(args.start, args.end):
						wr.writerow(self.get_user_row(date, user_id, extern_version))

					wr.writerow(["Sum h:mm", f"{time_spent // 60}h{time_spent % 60:02}m"] + 3 *[""])
					wr.writerow(["Sum h", round_up(time_spent / 60, 2)] + 3 * [""])
				logging.info(f"Printed {'official' if extern_version else ''} csv timesheet for user {username}")
			except OSError as e:
				logging.error(f"{str(e)} for user timesheet of {username}")

	def print_csv_total(self) -> None:
		"""
		Print time spents by all users to a CSV file

		Header: Date LIST_OF_USERNAMES

		Footer: Sum Min
				Sum h
		"""
		try:
			with open("total-timesheet.csv", "w", newline="", encoding="utf8") as csvfile:
				wr = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)

				wr.writerow(["Date"] + [username for username in self.users().values()])
				for date in date_range(args.start, args.end):
					wr.writerow([date] + [str(self.sum_date_user(date, user_id)) for user_id in self.users()])

				time_spents = [self.sum_user(user_id) for user_id in issues_mrs.users()]
				wr.writerow(["Sum h:mm"] + [f"{t // 60}h{t % 60:02}m" for t in time_spents])
				wr.writerow(["Sum h"] + [str(round_up(t / 60, 2)) for t in self.users()])
			logging.info(f"Printed total timesheet")
		except OSError as e:
			logging.warning(f"{str(e)} for total timesheet")


#######################################################################################################################
# Functions for processing a GitLab issue
#######################################################################################################################


def extract_date(note: str) -> datetime.date:
	"""Return a date object from a string of format 'at YYYY-MM-DD'."""
	return datetime.date.fromisoformat(re.search(r"(?<=at )\d{4}-\d{2}-\d{2}$", note).group())


def extract_time_spent(note: str) -> int:
	"""Return time spent in minutes from a gitlab time spent note."""
	type, time_spent_str = re.search(r"^(added|subtracted) (.*) of time spent", note).groups()
	if type not in ["added", "subtracted"]:
		msg = f"extract_time_spent: type {type}, time_spent_str {time_spent_str}, note {note}"
		raise NotImplementedError(msg).with_traceback(sys.exc_info()[2])

	# Map units of time to minutes.
	#
	# A time entry can have the following format:
	#   1mo 2w 3d 4h 5m 6s
	time_translations = {
		"mo": 9600,  # 1 working month consists of 4 working weeks
		"w": 2400,  # 1 working week consists of 5 working days
		"d": 480,  # 1 working day consists of 8 hours
		"h": 60,
		"m": 1,
		"s": 1 / 60,
	}

	time_spent = 0
	time_spent_array = time_spent_str.split(" ")
	for time_spent_part in time_spent_array:
		# "mo" is the only two-character unit and requires different handling.
		if time_spent_part[-2:] == "mo":
			time_spent += int(time_spent_part[:-2]) * time_translations["mo"]
		else:
			time_spent += int(time_spent_part[:-1]) * time_translations[time_spent_part[-1:]]

	# Negate calculated time spent if the keyword subtracted is used
	if type == "subtracted":
		time_spent = -time_spent

	return time_spent


def process_issue_mr(issue_mr: IssueOrMergeRequest,
					 gl: gitlab.Gitlab,
					 issues_mrs: IssueAndMrList) -> None:
	"""
	Thread function to extract when (date) a user worked on which issue/project and save it to Data.

	This function filters the issue and "decides" whether it will be added to the note list.
	"""
	if isinstance(issue_mr, gitlab.v4.objects.ProjectIssue):
		p_issue_mr = gl.projects.get(issue_mr.project_id, lazy=True).issues.get(issue_mr.iid)
	elif isinstance(issue_mr, gitlab.v4.objects.ProjectMergeRequest):
		p_issue_mr = gl.projects.get(issue_mr.project_id, lazy=True).mergerequests.get(issue_mr.iid)
	else:
		raise NotImplementedError

	if datetime.date.fromisoformat(p_issue_mr.updated_at[:10]) < args.start:
		return

	notes = p_issue_mr.notes.list(all=True)
	for note in notes:
		if note.system and "time spent" in note.body:
			created_at = datetime.datetime.fromisoformat(note.created_at.split('.', 1)[0])

			# Issues are orderd by recency. A time spent can not be made for the future.
			if created_at.date() < args.start:
				break

			user = note.author["username"]

			if args.users_blacklist and args.user and user in args.users:
				continue
			if args.users and user not in args.users:
				continue

			time_spent = extract_time_spent(note.body)

			# Older time spents have no date, use the creation date in that case
			try:
				spend_date = extract_date(note.body)
			except AttributeError:
				spend_date = datetime.date.fromisoformat(note.created_at[:10])

			# Do not add spend_date outside of the time range.
			# It is not possible to quit the iteration early although the notes are ordered by creation_date, because:
			#    - spend_date can be before the creation_date (spend_date < creation_date possible)
			#    - prior spend_date could be edited to a date after the creation_date (creation_date < spend_date possible)
			#    => spend_dates of notes could not be sorted by date
			if spend_date < args.start:
				continue
			if args.end < spend_date:
				continue

			# The spend time is not meaningful if it was not added on the same day.
			spend_datetime = created_at if created_at.date() == spend_date else None

			issues_mrs.update(Note(project, issue_mr, note.author, time_spent, spend_date, spend_datetime))


#######################################################################################################################
# Functions for parsing arguments, setting logging options and checking parsed arguments
#######################################################################################################################


def parse_args() -> argparse.Namespace:
	"""Initialize ArgumentParser and return parsed arguments."""

	class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter):
		"""Customize visualization of parameters in --help."""
		def _format_action_invocation(self, action):
			if not action.option_strings:
				default = self._get_default_metavar_for_positional(action)
				metavar, = self._metavar_formatter(action, default)(1)
				return metavar
			# new format is:
			#    -s, --long
			return ", ".join(action.option_strings)

	# Default timespan is this month
	today = datetime.date.today()
	first_this_month = today.replace(day=1)

	arg_parser = argparse.ArgumentParser(description="Generate timesheet CSVs from GitLab time spent entries.",
	                                     formatter_class=CustomFormatter)
	arg_parser.add_argument("-d", "--debug", action="store_true",
	                        help="enable debug script output on STDOUT")
	arg_parser.add_argument("-u", "--url", default="https://gitlab.com",
	                        help="url of GitLab instance starting with http or https")
	arg_parser.add_argument("-t", "--token", default=None,
	                        help="GitLab API access token")
	arg_parser.add_argument("--os_env", default="GITLAB_TOKEN",
	                        help="gather GitLab API access token from os.environ[*] (fallback if -t is not set)")
	arg_parser.add_argument("--threads", default=10, type=int,
	                        help="amount of simultaneous threads for the HTTP requests")
	arg_parser.add_argument("-s", "--start", metavar="YYYY-MM-DD", default=first_this_month,
	                        help="only include time spents from this day on", type=datetime.date.fromisoformat)
	arg_parser.add_argument("-e", "--end", metavar="YYYY-MM-DD", default=today,
	                        help="only include time spents up to this day", type=datetime.date.fromisoformat)
	arg_parser.add_argument("--last_month", action="store_true",
	                        help="set the start and end date for last month")
	arg_parser.add_argument("--extern_version", action="store_true",
	                        help="will also print official user timesheets which do not have a issue column")
	arg_parser.add_argument("--users", default=None, type=str,
	                        help="create timesheets only for specific usernames splitted by /")
	arg_parser.add_argument("--max_hours", default=None, type=str,
	                        help="given max hours per week, the maximal possible hours per month are calculated. "
	                             "Can be used in combination with users by splitting with /")
	arg_parser.add_argument("--users_blacklist", action="store_true",
	                        help="users parameter is a blacklist")
	arg_parser.add_argument("--projects", default=None, type=str,
	                        help="filter time spents by project ids splitted by /")
	arg_parser.add_argument("--projects_blacklist", action="store_true",
	                        help="projects parameter is a blacklist")
	arg_parser.add_argument("--sum_users", action="store_true",
	                        help="prints the total time spent of each user on STDOUT")
	arg_parser.add_argument("--sum_projects", action="store_true",
	                        help="prints the total time spent on each project on STDOUT")
	arg_parser.add_argument("--total_timesheet", action="store_true",
	                        help="print CSV file with all users")
	arg_parser.add_argument("--tex", action="store_true",
	                        help="format special characters in CSV output for TeX usage")

	args = arg_parser.parse_args()

	if args.last_month:
		args.end = first_this_month - datetime.timedelta(days=1)
		args.start = args.end.replace(day=1)

	if args.start > args.end:
		msg = f"Start date ({args.start}) is after end date ({args.end})."
		raise argparse.ArgumentTypeError(msg)

	return args


def split_arg(arg: str, return_type = None) -> list:
	"""
	Split arg by / and return it as a list.
	If return_type is set, the list is returned as return_type.

	arg_name and blacklist are needed to print the correct logging.info messages.
	"""
	if arg:
		arg = arg.replace(" ", "").split("/")

		if return_type:
			return [return_type(x) for x in arg]
		else:
			return arg
	return []


def last_day_of_month(any_day: datetime.date) -> datetime.date:
	"""Return last day of month"""
	next_month = any_day.replace(day=28) + datetime.timedelta(days=4)
	return next_month - datetime.timedelta(days=next_month.day)


def per_month(hours: list, start: datetime.date, end: datetime.date) -> None:
	"""
	Calculate max hours per week to max hours per month

	Bafög Amt says each month has 4 weeks.
	"""
	# Calculate working days
	#daygenerator = (start + timedelta(x) for x in range((last_day_of_month(end) - start).days + 1))
	#working_days = sum(day.weekday() < 5 for day in daygenerator)
	#
	for i in range(len(hours)):
		hours[i] = round(4 * hours[i], 2)
	#	hours[i] = round((hours[i] / 5) * working_days, 2)


def log_arg_list_debug(arg: list, arg_name: str, blacklist: bool) -> None:
	"""Log a debug message for a list argument."""
	if arg:
		plural_s = ""
		if len(arg) > 1:
			plural_s = "s"
		if blacklist:
			logging.debug(f"Getting time spents *not* from {arg_name}{plural_s} {', '.join(map(str, arg))}")
		else:
			logging.debug(f"Getting time spents only from {arg_name}{plural_s} {', '.join(map(str, arg))}")
	else:
		logging.debug(f"No {arg_name}s handed")


def log_arg_bool_debug(arg: str, arg_name: str) -> None:
	"""Log a debug message for an argument."""
	logging.debug(f"Argument {arg_name} is {arg}")


def _connect_gitlab(url: str, private_token: str, threads: int) -> gitlab.Gitlab:
	"""Return a gitlab.Gitlab connection using the token."""
	session = None
	if threads:
		session = requests.Session()
		adapter = requests.adapters.HTTPAdapter(pool_connections=threads, pool_maxsize=threads)
		session.mount('http://', adapter)
		session.mount('https://', adapter)

	gl = gitlab.Gitlab(url=url, private_token=private_token, session=session)

	try:
		gl.auth()
	except gitlab.exceptions.GitlabAuthenticationError as e:
		e.error_message += f" {url}"
		raise e

	return gl


def connect_gitlab(url: str, private_token: str, os_env: str, threads: int) -> (gitlab.Gitlab, str):
	"""Return a gitlab.Gitlab connection which is established using the token or os_env."""

	if private_token:
		gl = _connect_gitlab(url, private_token, threads)
	else:
		try:
			private_token = os.environ[os_env]
		except KeyError as e:
			logging.error(f"No valid GitLab token for {url} found in the environment variable {os_env}.")
			logging.error(f"Pass it with --token or set an environment variable.")
			raise e
		gl = _connect_gitlab(url, private_token, threads)

	logging.debug(f"Connected to {url}")
	logging.debug(f"Running with {threads} threads")

	return gl, private_token


def process_args() -> None:
	"""
	Print logging.debug messages for most arguments handed over by the ArgumentParser.
	Split arguments which are seperated by / by the user.
	"""
	logging.debug(f"Getting time spents from {args.start} to {args.end}")

	args.users = split_arg(args.users)
	args.projects = split_arg(args.projects, int)

	args.max_hours = split_arg(args.max_hours, float)
	per_month(args.max_hours, args.start, args.end)

	log_arg_bool_debug(args.last_month, "last_month")
	log_arg_bool_debug(args.extern_version, "extern_version")
	log_arg_list_debug(args.users, "user", args.users_blacklist)
	log_arg_bool_debug(args.max_hours, "max_hours")
	log_arg_list_debug(args.projects, "project", args.projects_blacklist)
	log_arg_bool_debug(args.sum_users, "sum_users")
	log_arg_bool_debug(args.sum_projects, "sum_projects")
	log_arg_bool_debug(args.total_timesheet, "total_timesheet")
	log_arg_bool_debug(args.tex, "tex")


#######################################################################################################################
# Main
#######################################################################################################################


if __name__ == '__main__':
	start_time_parsing = time.time()

	# Arguments are saved to the args namespace and do not have to be passed to other functions to be in scope
	args = parse_args()

	logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format="%(asctime)s   %(message)s")
	if args.debug:
		logging.debug("Debug mode activated")

	gl, args.token = connect_gitlab(args.url, args.token, args.os_env, args.threads)
	process_args()

	logging.info(f"Parsing finished after {time.time() - start_time_parsing} seconds\n")
	start_time_http_request = time.time()

	issues_mrs = IssueAndMrList()

	# It is not possible to edit or delete Issue objects. You need to create a ProjectIssue object to perform changes
	if args.projects:
		# Concrete filter for projects, query them directly.
		projects = [gl.projects.get(p) for p in args.projects]
	else:
		# Apply to all gitlab projects on the instance.
		if "gitlab.com" in args.url:
			print("Listing all projects in gitlab.com would take forever. Please specify the project ids with --projects")
			exit(1)
		logging.info(f"Listing all accessible gitlab projects in {args.url}")
		projects = gl.projects.list(all=True, lazy=True)

	for project in projects:
		if datetime.date.fromisoformat(project.last_activity_at[:10]) < args.start:
			continue
		if args.projects_blacklist and args.projects and project.id in args.projects:
			continue
		if args.projects and project.id not in args.projects:
			continue

		# Parallel process each issue in a single thread
		# The resulting data is saved in new_data and available by all threads
		executor = concurrent.futures.ThreadPoolExecutor(args.threads)

		issues = project.issues.list(all=True)
		mrs = project.mergerequests.list(all=True)

		issue_futures = [executor.submit(process_issue_mr, issue, gl, issues_mrs)
		                 for issue in issues if datetime.date.fromisoformat(issue.created_at[:10]) < args.end]
		mr_futures = [executor.submit(process_issue_mr, mr, gl, issues_mrs)
		              for mr in mrs if datetime.date.fromisoformat(mr.created_at[:10]) < args.end]

		all_futures = issue_futures + mr_futures
		concurrent.futures.wait(all_futures)
		for future in all_futures:
			future.result()  # So that exceptions are properly raised.

	logging.info(f"HTTP requests finished after {time.time() - start_time_http_request} seconds\n")
	start_time_printing = time.time()


	issues_mrs.print_csv_users()

	if args.extern_version:
		# TODO we could instead open the corresponding timesheet, remove the last column and save it with a
		#      different name, but this kind of method does not allow to print them separately
		issues_mrs.print_csv_users(args.extern_version)

	if args.total_timesheet:
		issues_mrs.print_csv_total()

	if args.sum_users or args.sum_projects:
		print("\n")
		print(f"Timespan: {args.start} - {args.end}")

	if args.sum_users:
		print("\n")
		for user_id, username in issues_mrs.users().items():
			time_spent = issues_mrs.sum_user(user_id)
			msg = f"Sum user {username}: {time_spent//60}h{time_spent%60:02}m({round_up(time_spent / 60, 2)}"
			if args.max_hours:
				if len(args.max_hours) == len(args.users):
					msg += f" / {args.max_hours[args.users.index(username)]}"
				else:
					msg += f" / {args.max_hours[0]}"

			print(msg + " h)")

	if args.sum_projects:
		print("\n")
		for project_id, project_name in issues_mrs.projects().items():
			time_spent = issues_mrs.sum_project(project_id)
			print(f"Sum project {project_name}: {time_spent//60}h{time_spent%60:02}m ({round(time_spent / 60, 2)} h)")

	if args.sum_users or args.sum_projects:
		print("\n")

	logging.info(f"Printing finished after {time.time() - start_time_printing} seconds")
	logging.info(f"Jobs done after {time.time() - start_time}")
