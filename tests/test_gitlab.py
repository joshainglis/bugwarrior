import ConfigParser
import datetime
from unittest import TestCase

import pytz
import responses

from bugwarrior.services.gitlab import GitlabService

from .base import ServiceTest, AbstractServiceTest


class TestGitlabService(TestCase):

    def setUp(self):
        self.config = ConfigParser.RawConfigParser()
        self.config.add_section('myservice')
        self.config.set('myservice', 'gitlab.login', 'foobar')

    def test_get_keyring_service_default_host(self):
        self.assertEqual(
            GitlabService.get_keyring_service(self.config, 'myservice'),
            'gitlab://foobar@gitlab.com')

    def test_get_keyring_service_custom_host(self):
        self.config.set('myservice', 'gitlab.host', 'gitlab.example.com')
        self.assertEqual(
            GitlabService.get_keyring_service(self.config, 'myservice'),
            'gitlab://foobar@gitlab.example.com')


class TestGitlabIssue(AbstractServiceTest, ServiceTest):
    maxDiff = None
    SERVICE_CONFIG = {
        'gitlab.host': 'gitlab.example.com',
        'gitlab.login': 'arbitrary_login',
        'gitlab.token': 'arbitrary_token',
    }
    arbitrary_created = (
        datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    ).replace(tzinfo=pytz.UTC, microsecond=0)
    arbitrary_updated = datetime.datetime.utcnow().replace(
        tzinfo=pytz.UTC, microsecond=0)
    arbitrary_issue = {
        "id": 42,
        "iid": 3,
        "project_id": 8,
        "title": "Add user settings",
        "description": "",
        "labels": [
            "feature"
        ],
        "milestone": {
            "id": 1,
            "title": "v1.0",
            "description": "",
            "due_date": "2012-07-20",
            "state": "closed",
            "updated_at": "2012-07-04T13:42:48Z",
            "created_at": "2012-07-04T13:42:48Z"
        },
        "assignee": {
            "id": 2,
            "username": "jack_smith",
            "email": "jack@example.com",
            "name": "Jack Smith",
            "state": "active",
            "created_at": "2012-05-23T08:01:01Z"
        },
        "author": {
            "id": 1,
            "username": "john_smith",
            "email": "john@example.com",
            "name": "John Smith",
            "state": "active",
            "created_at": "2012-05-23T08:00:58Z"
        },
        "state": "opened",
        "updated_at": arbitrary_updated.isoformat(),
        "created_at": arbitrary_created.isoformat(),
        "work_in_progress": True
    }
    arbitrary_extra = {
        'issue_url': 'https://gitlab.example.com/arbitrary_username/project/issues/3',
        'project': 'project',
        'type': 'issue',
        'annotations': [],
    }

    def setUp(self):
        self.service = self.get_mock_service(GitlabService)

    def test_normalize_label_to_tag(self):
        issue = self.service.get_issue_for_record(
            self.arbitrary_issue,
            self.arbitrary_extra
        )
        self.assertEqual(issue._normalize_label_to_tag('needs work'),
                         'needs_work')

    def test_to_taskwarrior(self):
        self.service.import_labels_as_tags = True
        issue = self.service.get_issue_for_record(
            self.arbitrary_issue,
            self.arbitrary_extra
        )

        expected_output = {
            'project': self.arbitrary_extra['project'],
            'priority': self.service.default_priority,
            'annotations': [],
            'tags': [u'feature'],
            issue.URL: self.arbitrary_extra['issue_url'],
            issue.REPO: 'project',
            issue.STATE: self.arbitrary_issue['state'],
            issue.TYPE: self.arbitrary_extra['type'],
            issue.TITLE: self.arbitrary_issue['title'],
            issue.NUMBER: self.arbitrary_issue['iid'],
            issue.UPDATED_AT: self.arbitrary_updated.replace(microsecond=0),
            issue.CREATED_AT: self.arbitrary_created.replace(microsecond=0),
            issue.DESCRIPTION: self.arbitrary_issue['description'],
            issue.MILESTONE: self.arbitrary_issue['milestone']['title'],
            issue.UPVOTES: 0,
            issue.DOWNVOTES: 0,
            issue.WORK_IN_PROGRESS: 0,
            issue.AUTHOR: 'john_smith',
            issue.ASSIGNEE: 'jack_smith',
        }
        actual_output = issue.to_taskwarrior()

        self.assertEqual(actual_output, expected_output)

    @responses.activate
    def test_issues(self):
        self.add_response(
            'https://gitlab.example.com/api/v3/projects?per_page=100&page=1',
            json=[{
                'id': 1,
                'path': 'arbitrary_username/project',
                'web_url': 'example.com'
            }])

        self.add_response(
            'https://gitlab.example.com/api/v3/projects/1/issues?per_page=100&page=1',
            json=[self.arbitrary_issue])

        self.add_response(
            'https://gitlab.example.com/api/v3/projects/1/issues/42/notes?per_page=100&page=1',
            json=[{
                'author': {'username': 'john_smith'},
                'body': 'Some comment.'
            }])

        issue = next(self.service.issues())

        expected = {
            'annotations': [u'@john_smith - Some comment.'],
            'description':
                u'(bw)Is#3 - Add user settings .. example.com/issues/3',
            'gitlabassignee': u'jack_smith',
            'gitlabauthor': u'john_smith',
            'gitlabcreatedon': self.arbitrary_created,
            'gitlabdescription': u'',
            'gitlabdownvotes': 0,
            'gitlabmilestone': u'v1.0',
            'gitlabnumber': 3,
            'gitlabrepo': u'arbitrary_username/project',
            'gitlabstate': u'opened',
            'gitlabtitle': u'Add user settings',
            'gitlabtype': 'issue',
            'gitlabupdatedat': self.arbitrary_updated,
            'gitlabupvotes': 0,
            'gitlaburl': u'example.com/issues/3',
            'gitlabwip': 0,
            'priority': 'M',
            'project': u'arbitrary_username/project',
            'tags': []}

        self.assertEqual(issue.get_taskwarrior_record(), expected)
