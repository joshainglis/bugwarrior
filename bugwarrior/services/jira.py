from __future__ import absolute_import

from datetime import datetime

import six
from jinja2 import Template
from jira.client import JIRA as BaseJIRA
from requests.cookies import RequestsCookieJar

from bugwarrior.config import asbool, die
from bugwarrior.services import IssueService, Issue

class ObliviousCookieJar(RequestsCookieJar):
    def set_cookie(self, *args, **kwargs):
        """Simply ignore any request to set a cookie."""
        pass

    def copy(self):
        """Make sure to return an instance of the correct class on copying."""
        return ObliviousCookieJar()


# 'Borrowed' from this wonderful commit by @GaretJax
# https://github.com/GaretJax/lancet/commit/f175cb2ec9a2135fb78188cf0b9f621b51d88977
# Prevents Jira web client being logged out when API call is made.
class JIRA(BaseJIRA):
    def _create_http_basic_session(self, username, password):
        super(JIRA, self)._create_http_basic_session(username, password)

        # XXX: JIRA logs the web user out if we send the session cookies we get
        # back from the first request in any subsequent requests. As we don't
        # need cookies when accessing the API anyway, just ignore all of them.
        self._session.cookies = ObliviousCookieJar()

    def close(self):
        self._session.close()


class JiraIssue(Issue):
    SUMMARY = 'jirasummary'
    URL = 'jiraurl'
    FOREIGN_ID = 'jiraid'
    DESCRIPTION = 'jiradescription'
    ESTIMATE = 'jiraestimate'
    FIX_VERSION = 'jirafixversion'
    CREATED_AT = 'jiracreatedts'

    UDAS = {
        SUMMARY: {
            'type': 'string',
            'label': 'Jira Summary'
        },
        URL: {
            'type': 'string',
            'label': 'Jira URL',
        },
        DESCRIPTION: {
            'type': 'string',
            'label': 'Jira Description',
        },
        FOREIGN_ID: {
            'type': 'string',
            'label': 'Jira Issue ID'
        },
        ESTIMATE: {
            'type': 'numeric',
            'label': 'Estimate'
        },
        FIX_VERSION: {
            'type': 'string',
            'label': 'Fix Version'
        },
        CREATED_AT: {
            'type': 'date',
            'label': 'Created At'
        },
    }
    UNIQUE_KEY = (URL, )

    PRIORITY_MAP = {
        'Highest': 'H',
        'High': 'H',
        'Medium': 'M',
        'Low': 'L',
        'Lowest': 'L',
        'Trivial': 'L',
        'Minor': 'L',
        'Major': 'M',
        'Critical': 'H',
        'Blocker': 'H',
    }

    def to_taskwarrior(self):
        return {
            'project': self.get_project(),
            'priority': self.get_priority(),
            'annotations': self.get_annotations(),
            'tags': self.get_tags(),
            'entry': self.get_entry(),

            self.URL: self.get_url(),
            self.FOREIGN_ID: self.record['key'],
            self.DESCRIPTION: self.record.get('fields', {}).get('description'),
            self.SUMMARY: self.get_summary(),
            self.ESTIMATE: self.get_estimate(),
            self.FIX_VERSION: self.get_fix_version()
        }

    def get_entry(self):
        created_at = self.record['fields']['created']

        # strip off the timezone offset
        timestamp, timezone = created_at[:-5], created_at[-5:]
        timestamp = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S.%f')
        return timestamp.strftime('%Y%m%dT%H%M%S') + timezone

    def get_tags(self):
        tags = []

        if not self.origin['import_labels_as_tags']:
            return tags

        context = self.record.copy()
        label_template = Template(self.origin['label_template'])

        for label in self.record.get('fields', {}).get('labels', []):
            context.update({
                'label': label
            })
            tags.append(
                label_template.render(context)
            )

        return tags

    def get_annotations(self):
        return self.extra.get('annotations', [])

    def get_project(self):
        return self.record['key'].rsplit('-', 1)[0]

    def get_number(self):
        return self.record['key'].rsplit('-', 1)[1]

    def get_url(self):
        return self.origin['url'] + '/browse/' + self.record['key']

    def get_summary(self):
        if self.extra.get('jira_version') == 4:
            return self.record['fields']['summary']['value']
        return self.record['fields']['summary']

    def get_estimate(self):
        if self.extra.get('jira_version') == 4:
            return self.record['fields']['timeestimate']['value']
        try:
            return self.record['fields']['timeestimate'] / 60 / 60
        except (TypeError, KeyError):
            return None

    def get_priority(self):
        value = self.record['fields'].get('priority')
        try:
            value = value['name']
        except (TypeError, ):
            value = str(value)
        return self.PRIORITY_MAP.get(value, self.origin['default_priority'])

    def get_default_description(self):
        return self.build_default_description(
            title=self.get_summary(),
            url=self.get_processed_url(self.get_url()),
            number=self.get_number(),
            cls='issue',
        )

    def get_fix_version(self):
        try:
            return self.record['fields'].get('fixVersions', [{}])[0].get('name')
        except (IndexError, KeyError, AttributeError, TypeError):
            return None


class JiraService(IssueService):
    ISSUE_CLASS = JiraIssue
    CONFIG_PREFIX = 'jira'

    def __init__(self, *args, **kw):
        super(JiraService, self).__init__(*args, **kw)
        self.username = self.config_get('username')
        self.url = self.config_get('base_uri')
        password = self.config_get_password('password', self.username)

        default_query = 'assignee=' + self.username + \
            ' AND resolution is null'
        self.query = self.config_get_default('query', default_query)
        self.jira = JIRA(
            options={
                'server': self.config_get('base_uri'),
                'rest_api_version': 'latest',
                'verify': self.config_get_default('verify_ssl', default=None, to_type=asbool),
            },
            basic_auth=(self.username, password)
        )
        self.import_labels_as_tags = self.config_get_default(
            'import_labels_as_tags', default=False, to_type=asbool
        )
        self.label_template = self.config_get_default(
            'label_template', default='{{label}}', to_type=six.text_type
        )

    @classmethod
    def get_keyring_service(cls, config, section):
        username = config.get(section, cls._get_key('username'))
        base_uri = config.get(section, cls._get_key('base_uri'))
        return "jira://%s@%s" % (username, base_uri)

    def get_service_metadata(self):
        return {
            'url': self.url,
            'import_labels_as_tags': self.import_labels_as_tags,
            'label_template': self.label_template,
        }

    @classmethod
    def validate_config(cls, config, target):
        for option in ('jira.username', 'jira.password', 'jira.base_uri'):
            if not config.has_option(target, option):
                die("[%s] has no '%s'" % (target, option))

        IssueService.validate_config(config, target)

    def annotations(self, issue, issue_obj):
        comments = self.jira.comments(issue.key)

        if not comments:
            return []
        else:
            return self.build_annotations(
                ((
                    comment.author.name,
                    comment.body
                ) for comment in comments),
                issue_obj.get_processed_url(issue_obj.get_url())
            )

    def issues(self):
        cases = self.jira.search_issues(self.query, maxResults=-1)

        jira_version = 5
        if self.config.has_option(self.target, 'jira.version'):
            jira_version = self.config.getint(self.target, 'jira.version')

        for case in cases:
            issue = self.get_issue_for_record(case.raw)
            extra = {
                'jira_version': jira_version,
            }
            if jira_version > 4:
                extra.update({
                    'annotations': self.annotations(case, issue)
                })
            issue.update_extra(extra)
            yield issue
