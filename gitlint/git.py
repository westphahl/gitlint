import arrow
import sh
# import exceptions separately, this makes it a little easier to mock them out in the unit tests
from sh import CommandNotFound, ErrorReturnCode

from gitlint.utils import ustr, sstr


class GitContextError(Exception):
    """ Exception indicating there is an issue with the git context """
    pass


class GitNotInstalledError(GitContextError):
    def __init__(self):
        super(GitNotInstalledError, self).__init__(
            u"'git' command not found. You need to install git to use gitlint on a local repository. " +
            u"See https://git-scm.com/book/en/v2/Getting-Started-Installing-Git on how to install git.")


def git_version():
    """ Determine the git version installed on this host by calling git --version"""
    try:
        version = ustr(sh.git("--version")).replace(u"\n", u"")
    except CommandNotFound:
        raise GitNotInstalledError()
    except ErrorReturnCode as e:  # Something went wrong while executing the git command
        error_msg = e.stderr.strip()
        error_msg = u"An error occurred while executing '{0}': {1}".format(e.full_cmd, error_msg)
        raise GitContextError(error_msg)
    return version


def git_commentchar():
    """ Shortcut for retrieving comment char from git config
    """
    try:
        commentchar = ustr(sh.git.config('--get', 'core.commentchar')).replace(u"\n", u"")
    except sh.ErrorReturnCode_1:  # pylint: disable=no-member
        # exception means that default commentchar used
        commentchar = '#'
    return commentchar


class GitCommitMessage(object):
    """ Class representing a git commit message. A commit message consists of the following:
      - original: The actual commit message as returned by `git log`
      - full: original, but stripped of any comments
      - title: the first line of full
      - body: all lines following the title
    """
    COMMENT_CHAR = git_commentchar()
    CUTLINE = '{0} ------------------------ >8 ------------------------'.format(COMMENT_CHAR)

    def __init__(self, original=None, full=None, title=None, body=None):
        self.original = original
        self.full = full
        self.title = title
        self.body = body

    @staticmethod
    def from_full_message(commit_msg_str):
        """  Parses a full git commit message by parsing a given string into the different parts of a commit message """
        all_lines = commit_msg_str.splitlines()
        try:
            cutline_index = all_lines.index(GitCommitMessage.CUTLINE)
        except ValueError:
            cutline_index = None
        lines = [line for line in all_lines[:cutline_index] if not line.startswith(GitCommitMessage.COMMENT_CHAR)]
        full = "\n".join(lines)
        title = lines[0] if len(lines) > 0 else ""
        body = lines[1:] if len(lines) > 1 else []
        return GitCommitMessage(original=commit_msg_str, full=full, title=title, body=body)

    def __unicode__(self):
        return self.full  # pragma: no cover

    def __str__(self):
        return sstr(self.__unicode__())  # pragma: no cover

    def __repr__(self):
        return self.__str__()  # pragma: no cover

    def __eq__(self, other):
        return isinstance(other, GitCommitMessage) and self.original == other.original and \
               self.full == other.full and self.title == other.title and self.body == other.body  # noqa


class GitCommit(object):
    """ Class representing a git commit.
        A commit consists of: context, message, author name, author email, date, list of changed files
        In the context of gitlint, only the git context and commit message are required.
    """

    def __init__(self, context, message, sha=None, date=None, author_name=None, author_email=None, parents=None,
                 is_merge_commit=False, changed_files=None):
        self.context = context
        self.message = message
        self.sha = sha
        self.date = date
        self.author_name = author_name
        self.author_email = author_email
        # parent commit hashes
        self.parents = parents or []
        self.is_merge_commit = is_merge_commit
        self.changed_files = changed_files or []

    def __unicode__(self):
        format_str = u"Author: %s <%s>\nDate:   %s\n%s"  # pragma: no cover
        return format_str % (self.author_name, self.author_email, self.date, ustr(self.message))  # pragma: no cover

    def __str__(self):
        return sstr(self.__unicode__())  # pragma: no cover

    def __repr__(self):
        return self.__str__()  # pragma: no cover

    def __eq__(self, other):
        # skip checking the context as context refers back to this obj, this will trigger a cyclic dependency
        return isinstance(other, GitCommit) and self.message == other.message and \
               self.sha == other.sha and self.author_name == other.author_name and \
               self.author_email == other.author_email and \
               self.date == other.date and self.parents == other.parents and \
               self.is_merge_commit == other.is_merge_commit and self.changed_files == other.changed_files  # noqa


class GitContext(object):
    """ Class representing the git context in which gitlint is operating: a data object storing information about
    the git repository that gitlint is linting.
    """

    def __init__(self):
        self.commits = []

    @staticmethod
    def from_commit_msg(commit_msg_str):
        """ Determines git context based on a commit message.
        :param commit_msg_str: Full git commit message.
        """
        context = GitContext()
        commit_msg_obj = GitCommitMessage.from_full_message(commit_msg_str)

        # For now, we consider a commit a merge commit if its title starts with "Merge"
        is_merge_commit = commit_msg_obj.title.startswith("Merge")
        commit = GitCommit(context, commit_msg_obj, is_merge_commit=is_merge_commit)

        context.commits.append(commit)
        return context

    @staticmethod
    def from_local_repository(repository_path, refspec=None):
        """ Retrieves the git context from a local git repository.
        :param repository_path: Path to the git repository to retrieve the context from
        :param refspec: The commit(s) to retrieve
        """

        context = GitContext()
        try:
            # Special arguments passed to sh: http://amoffat.github.io/sh/special_arguments.html
            sh_special_args = {
                '_tty_out': False,
                '_cwd': repository_path
            }

            if refspec is None:
                # We tried many things here e.g.: defaulting to e.g. HEAD or HEAD^... (incl. dealing with
                # repos that only have a single commit - HEAD^... doesn't work there), but then we still get into
                # problems with e.g. merge commits. Easiest solution is just taking the SHA from `git log -1`.
                sha_list = [sh.git.log("-1", "--pretty=%H", **sh_special_args).replace("\n", "")]
            else:
                sha_list = sh.git("rev-list", refspec, **sh_special_args).split()

            for sha in sha_list:
                # Get info from the local git repository: https://git-scm.com/docs/pretty-formats
                raw_commit = sh.git.log(sha, "-1", "--pretty=%aN,%aE,%ai,%P%n%B",
                                        **sh_special_args).split("\n")

                (name, email, date, parents), commit_msg = raw_commit[0].split(","), "\n".join(raw_commit[1:])

                commit_parents = parents.split(" ")
                commit_is_merge_commit = len(commit_parents) > 1

                # changed files in last commit
                changed_files = sh.git("diff-tree", "--no-commit-id", "--name-only",
                                       "-r", sha, **sh_special_args).split()

                # "YYYY-MM-DD HH:mm:ss Z" -> ISO 8601-like format
                # Use arrow for datetime parsing, because apparently python is quirky around ISO-8601 dates:
                # http://stackoverflow.com/a/30696682/381010
                commit_date = arrow.get(ustr(date), "YYYY-MM-DD HH:mm:ss Z").datetime

                # Create Git commit object with the retrieved info
                commit_msg_obj = GitCommitMessage.from_full_message(commit_msg)
                commit = GitCommit(context, commit_msg_obj, sha=sha, author_name=name,
                                   author_email=email, date=commit_date, changed_files=changed_files,
                                   parents=commit_parents, is_merge_commit=commit_is_merge_commit)

                context.commits.append(commit)

        except CommandNotFound:
            raise GitNotInstalledError()
        except ErrorReturnCode as e:  # Something went wrong while executing the git command
            error_msg = e.stderr.strip()
            if b"Not a git repository" in error_msg:
                error_msg = u"{0} is not a git repository.".format(repository_path)
            else:
                error_msg = u"An error occurred while executing '{0}': {1}".format(e.full_cmd, error_msg)
            raise GitContextError(error_msg)

        return context

    def __eq__(self, other):
        return isinstance(other, GitContext) and self.commits == other.commits
