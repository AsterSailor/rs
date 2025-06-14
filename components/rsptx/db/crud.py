# *************************************************
# |docname| - reusable functions for our data model
# *************************************************
"""
Create Retrieve Update and Delete (CRUD) functions for database tables

Rather than litter the code with raw database queries the vast majority should be
turned into reusable functions that are defined in this file.

"""
# Imports
# =======
# These are listed in the order prescribed by `PEP 8`_.
#
# Standard library
# ----------------
import datetime
import hashlib
import json
from collections import namedtuple
from typing import Dict, List, Optional, Tuple, Any
import textwrap
import traceback
import pytz
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import (
    select,
    func,
    and_,
    or_,
    not_,
    desc,
    asc,
    String,
    Text,
    Unicode,
    UnicodeText,
)

# Third-party imports
# -------------------
from asyncpg.exceptions import UniqueViolationError
from fastapi import status
from fastapi.exceptions import HTTPException
from pydal.validators import CRYPT
from sqlalchemy import and_, distinct, func, update, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import select, text, delete, insert
from sqlalchemy.orm import aliased, joinedload, contains_eager, attributes
from starlette.requests import Request

from rsptx.validation import schemas
from rsptx.validation.schemas import (
    AssignmentQuestionUpdateDict,
)

# Local application imports
# -------------------------
from rsptx.logging import rslogger
from rsptx.configuration import settings
from .async_session import async_session
from .sync_session import sync_session
from rsptx.response_helpers.core import http_422error_detail, canonical_utcnow
from rsptx.db.models import (
    Assignment,
    AssignmentValidator,
    AssignmentQuestion,
    AssignmentQuestionValidator,
    AuthGroup,
    AuthMembership,
    AuthUser,
    AuthUserValidator,
    BookAuthor,
    Chapter,
    ChapterValidator,
    ClickableareaAnswers,
    Code,
    CodeValidator,
    Competency,
    CourseAttribute,
    CourseInstructor,
    CourseInstructorValidator,
    CourseLtiMap,
    CoursePractice,
    Courses,
    CoursesValidator,
    DeadlineException,
    DeadlineExceptionValidator,
    DomainApprovals,
    DragndropAnswers,
    EditorBasecourse,
    FitbAnswers,
    Grade,
    GradeValidator,
    InvoiceRequest,
    Library,
    LibraryValidator,
    LtiKey,
    Lti1p3Conf,
    Lti1p3ConfValidator,
    Lti1p3Course,
    Lti1p3CourseValidator,
    Lti1p3User,
    Lti1p3UserValidator,
    Lti1p3Assignment,
    Lti1p3AssignmentValidator,
    MchoiceAnswers,
    ParsonsAnswers,
    Question,
    QuestionGrade,
    QuestionGradeValidator,
    QuestionValidator,
    runestone_component_dict,
    SelectedQuestion,
    SelectedQuestionValidator,
    ShortanswerAnswers,
    SourceCode,
    SourceCodeValidator,
    SubChapter,
    SubChapterValidator,
    TimedExam,
    TimedExamValidator,
    TraceBack,
    Useinfo,
    UseinfoValidation,
    UserChapterProgress,
    UserChapterProgressValidator,
    UserCourse,
    UserExperiment,
    UserExperimentValidator,
    UserState,
    UserStateValidator,
    UserSubChapterProgress,
    UserSubChapterProgressValidator,
    UserTopicPractice,
    UserTopicPracticeCompletion,
    UserTopicPracticeFeedback,
    UserTopicPracticeValidator,
    APIToken,
    APITokenValidator,
)
from rsptx.data_types.which_to_grade import WhichToGradeOptions
from rsptx.data_types.autograde import AutogradeOptions

# Map from the ``event`` field of a ``LogItemIncoming`` to the database table used to store data associated with this event.
EVENT2TABLE = {
    "clickableArea": "clickablearea_answers",
    "codelens": "codelens_answers",
    "dragNdrop": "dragndrop_answers",
    "fillb": "fitb_answers",
    "lp_build": "lp_answers",
    "matching": "matching_answers",
    "mChoice": "mchoice_answers",
    "parsons": "parsons_answers",
    "shortanswer": "shortanswer_answers",
    "unittest": "unittest_answers",
    "timedExam": "timed_exam",
    "webwork": "webwork_answers",
    "hparsonsAnswer": "microparsons_answers",
    "SPLICE.score": "splice_answers",
    "SPLICE.reportScoreAndState": "splice_answers",
    "SPLICE.getState": "splice_answers",
}


# useinfo
# -------
async def create_useinfo_entry(log_entry: UseinfoValidation) -> UseinfoValidation:
    """Add a row to the ``useinfo`` table.

    :param log_entry: Log entries contain a timestamp, an event, details about the event, a student id, and the identifier of the book element that was interacted with.
    :type log_entry: UseinfoValidation
    :return: A representation of the row inserted.
    :rtype: UseinfoValidation
    """
    async with async_session.begin() as session:
        new_entry = Useinfo(**log_entry.dict())
        rslogger.debug(f"timestamp = {log_entry.timestamp} ")
        rslogger.debug(f"New Entry = {new_entry}")
        rslogger.debug(f"session = {session}")
        session.add(new_entry)
    rslogger.debug(new_entry)
    return UseinfoValidation.from_orm(new_entry)


async def count_useinfo_for(
    div_id: str, course_name: str, start_date: datetime.datetime
) -> List[tuple]:
    """return a list of tuples that include the [(act, count), (act, count)]
    act is a freeform field in the useinfo table that varies from event
    type to event type.

    :param div_id: Unique identifier of a Runestone component
    :type div_id: str
    :param course_name: The current course
    :type course_name: str
    :param start_date:
    :type start_date: datetime.datetime
    :return: A list of tuples [(act, count), (act), count)]
    :rtype: List[tuple]
    """
    query = (
        select(Useinfo.act, func.count(Useinfo.act).label("count"))
        .where(
            (Useinfo.div_id == div_id)
            & (Useinfo.course_id == course_name)
            & (Useinfo.timestamp > start_date)
        )
        .group_by(Useinfo.act)
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"res = {res}")
        return res.all()


async def get_peer_votes(div_id: str, course_name: str, voting_stage: int):
    """
    Provide the answers for a peer instruction multiple choice question.
    What percent of students chose each option. This is used for the Review page of Peer Instruction questions.
    """
    # Subquery to get the latest vote for each student
    subquery = (
        select(func.max(Useinfo.id).label("max_id"))
        .where(
            (Useinfo.event == "mChoice")
            & (Useinfo.course_id == course_name)
            & (Useinfo.div_id == div_id)
            & Useinfo.act.like(f"%vote{voting_stage}")
        )
        # Group by student ID to get each student's latest vote
        .group_by(Useinfo.sid)
        .subquery()
    )

    # Querying the Useinfo table for every student's latest votes using the subquery
    query = (
        select(Useinfo.act)
        .join(subquery, Useinfo.id == subquery.c.max_id)
        .order_by(Useinfo.id.desc())
    )

    async with async_session() as session:
        result = await session.execute(query)
        ans = result.scalars().all()

    if ans:
        return {"acts": ans}
    else:
        return {"acts": []}


async def get_book_chapters(course_name: str) -> List[ChapterValidator]:
    """
    Retrieve all chapters for a given course (course_name)

    :param course_name: str, the name of the course
    :return: List[ChapterValidator], a list of ChapterValidator objects representing the chapters
    """
    query = (
        select(Chapter)
        .where(Chapter.course_id == course_name)
        .order_by(Chapter.chapter_num)
    )
    async with async_session() as session:
        res = await session.execute(query)
        return [ChapterValidator.from_orm(x) for x in res.scalars().fetchall()]


async def fetch_chapter_for_subchapter(subchapter: str, base_course: str) -> str:
    """
    Used for pretext books where the subchapter is unique across the book
    due to the flat structure produced by pretext build.  In this case the
    old RST structure where we get the chapter and subchapter from the URL
    /book/chapter/subchapter.html gives us the wrong answer of the book.
    """

    query = (
        select(Chapter.chapter_label)
        .join(SubChapter, Chapter.id == SubChapter.chapter_id)
        .where(
            (Chapter.course_id == base_course)
            & (SubChapter.sub_chapter_label == subchapter)
        )
    )
    async with async_session() as session:
        chapter_label = await session.execute(query)
        return chapter_label.scalars().first()


async def get_book_subchapters(course_name: str) -> List[SubChapterValidator]:
    """
    Retrieve all subchapters for a given course (course_name)

    :param course_name: str, the name of the course
    :return: List[SubChapterValidator], a list of SubChapterValidator objects
    """
    query = (
        select(SubChapter)
        .join(Chapter)
        .where(
            (Chapter.course_id == course_name) & (SubChapter.chapter_id == Chapter.id)
        )
        .order_by(Chapter.chapter_num, SubChapter.sub_chapter_num)
    )
    async with async_session() as session:
        print(query)
        res = await session.execute(query)
        return [SubChapterValidator.from_orm(x) for x in res.scalars().fetchall()]


async def fetch_page_activity_counts(
    chapter: str, subchapter: str, base_course: str, course_name: str, username: str
) -> Dict[str, int]:
    """
    Used for the progress bar at the bottom of each page.  This function
    finds all of the components for a particular page (chaper/subchapter)
    and then finds out which of those elements the student has interacted
    with.  It returns a dictionary of {divid: 0/1}
    """

    where_clause_common = (
        (Question.subchapter == subchapter)
        & (Question.chapter == chapter)
        & (Question.from_source == True)  # noqa: E712
        & (
            (Question.optional == False)  # noqa: E712
            | (Question.optional == None)  # noqa: E711
        )
        & (Question.base_course == base_course)
    )

    query = select(Question).where(where_clause_common)

    async with async_session() as session:
        page_divids = await session.execute(query)
    rslogger.debug(f"PDVD {page_divids}")
    div_counts = {q.name: 0 for q in page_divids.scalars()}
    query = select(distinct(Useinfo.div_id)).where(
        where_clause_common
        & (Question.name == Useinfo.div_id)
        & (Useinfo.course_id == course_name)
        & (Useinfo.sid == username)
    )
    async with async_session() as session:
        sid_counts = await session.execute(query)

    # doing a call to scalars() on a single column join query like this reduces
    # the row to just the string.  So each row is just a string representing a unique
    # div_id the user has interacted with on this page.
    for row in sid_counts.scalars():
        div_counts[row] = 1

    return div_counts


# write a function that takes a QuestionValidator as a parameter and inserts a new Question into the database
async def create_question(question: QuestionValidator) -> QuestionValidator:
    """Add a row to the ``question`` table.

    :param question: A question object
    :type question: QuestionValidator
    :return: A representation of the row inserted.
    :rtype: QuestionValidator
    """
    async with async_session.begin() as session:
        new_question = Question(**question.dict())
        session.add(new_question)
    return QuestionValidator.from_orm(new_question)


async def update_question(question: QuestionValidator) -> QuestionValidator:
    """Update a row in the ``question`` table.

    :param question: A question object
    :type question: QuestionValidator
    :return: A representation of the row updated.
    :rtype: QuestionValidator
    """
    async with async_session.begin() as session:
        stmt = (
            update(Question).where(Question.id == question.id).values(**question.dict())
        )
        await session.execute(stmt)
    return question


async def fetch_poll_summary(div_id: str, course_name: str) -> List[tuple]:
    """
    Find the last answer for each student and then aggregate those answers to provide a summary of poll
    responses for the given question. For a poll, the value of act is a response number 0--N where N is
    the number of different choices.

    :param div_id: The div_id of the poll
    :type div_id: str
    :param course_name: The name of the course
    :type course_name: str
    :return: A list of tuples where the first element is the response number and the second element is the count
             of students who chose that response.
    :rtype: List[tuple]
    """
    query = text(
        """select act, count(*) from useinfo
        join (select sid, max(id) mid
        from useinfo where event='poll' and div_id = :div_id and course_id = :course_name group by sid) as T
        on id = T.mid group by act"""
    )

    async with async_session() as session:
        rows = await session.execute(
            query, params=dict(div_id=div_id, course_name=course_name)
        )
        return rows.all()


async def fetch_top10_fitb(dbcourse: CoursesValidator, div_id: str) -> List[tuple]:
    """
    Return the top 10 answers to a fill in the blank question.

    :param dbcourse: The course for which to retrieve the top answers.
    :type dbcourse: CoursesValidator
    :param div_id: The div_id of the fill in the blank question.
    :type div_id: str
    :return: A list of tuples where the first element is the answer and the second element is the count of times
             that answer was given.
    :rtype: List[tuple]
    """
    rcd = runestone_component_dict["fitb_answers"]
    tbl = rcd.model
    query = (
        select(tbl.answer, func.count(tbl.answer).label("count"))
        .where(
            (tbl.div_id == div_id)
            & (tbl.course_name == dbcourse.course_name)
            & (tbl.timestamp > dbcourse.term_start_date)
        )
        .group_by(tbl.answer)
        .order_by(func.count(tbl.answer).desc())
        .limit(10)
    )
    async with async_session() as session:
        rows = await session.execute(query)
        return rows.all()


# xxx_answers
# -----------
async def create_answer_table_entry(
    # The correct type is one of the validators for an answer table; we use LogItemIncoming as a generalization of this.
    log_entry: schemas.LogItemIncoming,
    # The event type.
    event: str,
) -> schemas.LogItemIncoming:
    """Populate the xxx_answers table with the incoming data

    :param log_entry:
    :type log_entry: schemas.LogItemIncoming
    :param event: Will be something like "mchoice", "parsons", etc.
    :type event: str
    :return: Returns the newly created item
    :rtype: schemas.LogItemIncoming
    """
    rslogger.debug(f"hello from create at {log_entry}")
    rcd = runestone_component_dict[EVENT2TABLE[event]]
    new_entry = rcd.model(**log_entry.dict())  # type: ignore
    async with async_session.begin() as session:
        session.add(new_entry)

    rslogger.debug(f"returning {new_entry}")
    return rcd.validator.from_orm(new_entry)  # type: ignore


async def fetch_last_answer_table_entry(
    query_data: schemas.AssessmentRequest,
) -> schemas.LogItemIncoming:
    """The xxx_answers table contains ALL of the answers a student has made for this question.  but most often all we want is the most recent answer

    :param query_data:
    :type query_data: schemas.AssessmentRequest
    :return: The most recent answer
    :rtype: schemas.LogItemIncoming
    """
    rcd = runestone_component_dict[EVENT2TABLE[query_data.event]]
    tbl = rcd.model
    deadline_offset_naive = query_data.deadline.replace(tzinfo=None)
    query = (
        select(tbl)
        .where(
            and_(
                tbl.div_id == query_data.div_id,
                tbl.course_name == query_data.course,
                tbl.sid == query_data.sid,
                tbl.timestamp <= deadline_offset_naive,
            )
        )
        .order_by(tbl.timestamp.desc())
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"res = {res}")
        return rcd.validator.from_orm(res.scalars().first())  # type: ignore


async def fetch_last_poll_response(sid: str, course_name: str, poll_id: str) -> str:
    """
    Return a student's (sid) last response to a given poll (poll_id)

    :param sid: str, the student id
    :param course_name: str, the name of the course
    :param poll_id: str, the id of the poll
    :return: str, the last response of the student for the given poll
    """
    query = (
        select(Useinfo.act)
        .where(
            (Useinfo.sid == sid)
            & (Useinfo.course_id == course_name)
            & (Useinfo.div_id == poll_id)
        )
        .order_by(Useinfo.id.desc())
    )
    async with async_session() as session:
        res = await session.execute(query)
        return res.scalars().first()


# Courses
# -------
async def fetch_course(course_name: str) -> CoursesValidator:
    """
    Fetches a course by its name.

    :param course_name: The name of the course to be fetched.
    :type course_name: str
    :return: A CoursesValidator instance representing the fetched course.
    :rtype: CoursesValidator
    """
    query = select(Courses).where(Courses.course_name == course_name)
    async with async_session() as session:
        res = await session.execute(query)
        # When selecting ORM entries it is useful to use the ``scalars`` method
        # This modifies the result so that you are getting the ORM object
        # instead of a Row object. `See <https://docs.sqlalchemy.org/en/14/orm/queryguide.html#selecting-orm-entities-and-attributes>`_
        course = res.scalars().one_or_none()
        return CoursesValidator.from_orm(course)


async def fetch_course_by_id(course_id: int) -> CoursesValidator:
    """
    Fetches a course by its id.

    :param course_name: The id of the course to be fetched.
    :type course_name: int
    :return: A CoursesValidator instance representing the fetched course.
    :rtype: CoursesValidator
    """
    query = select(Courses).where(Courses.id == course_id)
    async with async_session() as session:
        res = await session.execute(query)
        # When selecting ORM entries it is useful to use the ``scalars`` method
        # This modifies the result so that you are getting the ORM object
        # instead of a Row object. `See <https://docs.sqlalchemy.org/en/14/orm/queryguide.html#selecting-orm-entities-and-attributes>`_
        course = res.scalars().one_or_none()
        return CoursesValidator.from_orm(course)


async def fetch_base_course(base_course: str) -> CoursesValidator:
    """
    Fetches a base course by its name.

    :param base_course: The name of the base course to be fetched.
    :type base_course: str
    :return: A CoursesValidator instance representing the fetched base course.
    :rtype: CoursesValidator
    """
    query = select(Courses).where(
        (Courses.base_course == base_course) & (Courses.course_name == base_course)
    )
    async with async_session() as session:
        res = await session.execute(query)
        # When selecting ORM entries it is useful to use the ``scalars`` method
        # This modifies the result so that you are getting the ORM object
        # instead of a Row object. `See <https://docs.sqlalchemy.org/en/14/orm/queryguide.html#selecting-orm-entities-and-attributes>`_
        base_course = res.scalars().one_or_none()
        return CoursesValidator.from_orm(base_course)


async def create_course(course_info: CoursesValidator) -> None:
    """
    Creates a new course in the database.

    :param course_info: A CoursesValidator instance representing the course to be created.
    :type course_info: CoursesValidator
    :return: None
    """
    new_course = Courses(**course_info.dict())
    async with async_session.begin() as session:
        session.add(new_course)
    return new_course


async def user_in_course(user_id: int, course_id: int) -> bool:
    """
    Return true if given user is in indicated course

    :param user_id: int, the user id
    :param course_id: the id of the course
    :return: True / False
    """
    query = select(func.count(UserCourse.course_id)).where(
        and_(UserCourse.user_id == user_id, UserCourse.course_id == course_id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        res_count = res.scalars().fetchall()[0]
        return res_count != 0


async def fetch_courses_for_user(
    user_id: int, course_id: Optional[int] = None
) -> UserCourse:
    """
    Retrieve a list of courses for a given user (user_id)

    :param user_id: int, the user id
    :param course_id: Optional[int], the id of the course (optional)
    :return: List[UserCourse], a list of UserCourse objects representing the courses
    """
    if course_id is None:
        query = select(Courses).where(
            and_(UserCourse.user_id == user_id, UserCourse.course_id == Courses.id)
        )
    else:
        query = select(Courses).where(
            and_(
                UserCourse.user_id == user_id,
                UserCourse.course_id == course_id,
                UserCourse.course_id == Courses.id,
            )
        )
    async with async_session() as session:
        res = await session.execute(query)
        # When selecting ORM entries it is useful to use the ``scalars`` method
        # This modifies the result so that you are getting the ORM object
        # instead of a Row object. `See <https://docs.sqlalchemy.org/en/14/orm/queryguide.html#selecting-orm-entities-and-attributes>`_
        course_list = [CoursesValidator.from_orm(x) for x in res.scalars().fetchall()]
        return course_list


#
async def fetch_users_for_course(course_name: str) -> list[AuthUserValidator]:
    """
    Retrieve a list of users/students enrolled in a given course (course_name)

    :param course_name: str, the name of the course
    :return: list[AuthUserValidator], a list of AuthUserValidator objects representing the users
    """
    course = await fetch_course(course_name)
    query = select(AuthUser).where(
        and_(
            UserCourse.user_id == AuthUser.id,
            UserCourse.course_id == course.id,
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        # When selecting ORM entries it is useful to use the ``scalars`` method
        # This modifies the result so that you are getting the ORM object
        # instead of a Row object. `See <https://docs.sqlalchemy.org/en/14/orm/queryguide.html#selecting-orm-entities-and-attributes>`_
        user_list = [AuthUserValidator.from_orm(x) for x in res.scalars().fetchall()]
        return user_list


async def create_user_course_entry(user_id: int, course_id: int) -> UserCourse:
    """
    Create a new user course entry for a given user (user_id) and course (course_id)

    :param user_id: int, the user id
    :param course_id: int, the course id
    :return: UserCourse, the newly created UserCourse object
    """
    new_uc = UserCourse(user_id=user_id, course_id=course_id)
    async with async_session.begin() as session:
        session.add(new_uc)

    return new_uc

async def delete_user_course_entry(user_id: int, course_id: int) -> None:
    """
    Delete a user course entry for a given user (user_id) and course (course_id)

    :param user_id: int, the user id
    :param course_id: int, the course id
    """
    query = delete(UserCourse).where(
        and_(UserCourse.user_id == user_id, UserCourse.course_id == course_id)
    )
    async with async_session.begin() as session:
        await session.execute(query)
        
# course_attributes
# -----------------


async def fetch_all_course_attributes(course_id: int) -> dict:
    """
    Retrieve all attributes and their values for a given course (course_id)

    :param course_id: int, the id of the course
    :return: dict, a dictionary containing all course attributes and their values
    """
    query = select(CourseAttribute).where(CourseAttribute.course_id == course_id)

    async with async_session() as session:
        res = await session.execute(query)
        return {row.attr: row.value for row in res.scalars().fetchall()}


async def fetch_one_course_attribute():
    """
    Fetch a single course attribute (not implemented)

    :raises: NotImplementedError
    """
    raise NotImplementedError()


async def create_course_attribute(course_id: int, attr: str, value: str):
    """
    Create a new course attribute for a given course (course_id)

    :param course_id: int, the id of the course
    :param attr: str, the attribute name
    :param value: str, the attribute value
    """
    new_attr = CourseAttribute(course_id=course_id, attr=attr, value=value)
    async with async_session.begin() as session:
        session.add(new_attr)


async def copy_course_attributes(basecourse_id: int, new_course_id: int):
    """
    Copy all course attributes from a base course to a new course
    """
    query = select(CourseAttribute).where(CourseAttribute.course_id == basecourse_id)
    async with async_session() as session:
        res = await session.execute(query)
        for row in res.scalars().fetchall():
            print(row.attr, row.value)
            new_attr = CourseAttribute(
                course_id=new_course_id, attr=row.attr, value=row.value
            )
            session.add(new_attr)
        await session.commit()


async def get_course_origin(base_course):
    """
    Retrieve the origin of a given course (base_course)

    :param base_course: str, the name of the base course
    :return: str, the origin of the course
    """
    query = select(CourseAttribute).where(
        (CourseAttribute.course_id == base_course)
        & (CourseAttribute.attr == "markup_system")
    )

    async with async_session() as session:
        res = await session.execute(query)
        ca = res.scalars().first()
        return ca.value


# auth_user
# ---------
async def fetch_user(
    user_name: str, fallback_to_registration: bool = False
) -> AuthUserValidator:
    """
    Retrieve a user by their username (user_name)

    fallback_to_registration is for LTI logins to match existing instructor accounts.
    If user_name is not found, try to find the user by their registration_id (initial email).

    :param user_name: str, the username of the user
    :return: AuthUserValidator, the AuthUserValidator object representing the user
    """
    query = select(AuthUser).where(AuthUser.username == user_name)
    async with async_session() as session:
        res = await session.execute(query)
        user = res.scalars().one_or_none()
        if not user and fallback_to_registration:
            fallback_query = select(AuthUser).where(
                AuthUser.registration_id == user_name
            )
            res = await session.execute(fallback_query)
            user = res.scalars().one_or_none()
    return AuthUserValidator.from_orm(user)


async def create_user(user: AuthUserValidator) -> Optional[AuthUserValidator]:
    """
    The given user will have the password in plain text.  First we will hash
    the password then add this user to the database.

    :param user: AuthUserValidator, the AuthUserValidator object representing the user to be created
    :return: Optional[AuthUserValidator], the newly created AuthUserValidator object if successful, None otherwise
    """
    if await fetch_user(user.username):
        raise HTTPException(
            status_code=422,
            detail=http_422error_detail(
                ["body", "username"], "duplicate username", "integrity_error"
            ),
        )

    new_user = AuthUser(**user.dict())
    crypt = CRYPT(key=settings.web2py_private_key, salt=True)
    new_user.password = str(crypt(user.password)[0])
    async with async_session.begin() as session:
        session.add(new_user)
    return AuthUserValidator.from_orm(new_user)


async def update_user(user_id: int, new_vals: dict):
    """
    Update a user's information by their id (user_id)

    :param user_id: int, the id of the user
    :param new_vals: dict, a dictionary containing the new values to be updated
    """
    if "password" in new_vals:
        crypt = CRYPT(key=settings.web2py_private_key, salt=True)
        new_vals["password"] = str(crypt(new_vals["password"])[0])
    stmt = update(AuthUser).where((AuthUser.id == user_id)).values(**new_vals)
    async with async_session.begin() as session:
        await session.execute(stmt)
    rslogger.debug("SUCCESS")


async def delete_user(username):
    """
    Delete a user by their username (username)

    :param username: str, the username of the user to be deleted
    """
    # We do not have foreign key constraints on the username in the answer tables
    # so delete all of the rows matching the username schedule for deletion
    stmt_list = []
    for tbl, item in runestone_component_dict.items():
        stmt = delete(item.model).where(item.model.sid == username)
        stmt_list.append(stmt)

    delcode = delete(Code).where(Code.sid == username)
    deluse = delete(Useinfo).where(Useinfo.sid == username)
    deluser = delete(AuthUser).where(AuthUser.username == username)
    async with async_session.begin() as session:
        for stmt in stmt_list:
            await session.execute(stmt)
        await session.execute(delcode)
        await session.execute(deluse)
        await session.execute(deluser)
        # This will delete many other things as well based on the CASECADING
        # foreign keys


async def fetch_group(group_name):
    """
    Retrieve a group by its name (group_name)

    :param group_name: str, the name of the group
    :return: AuthGroup, the AuthGroup object representing the group
    """
    query = select(AuthGroup).where(AuthGroup.role == group_name)  # noqa: E712
    async with async_session() as session:
        res = await session.execute(query)
        # the result type of this query is a sqlalchemy CursorResult
        # .all will return a list of Rows
        ret = res.scalars().first()
        # the result of .scalars().first() is a single Library object

        return ret


async def create_group(group_name):
    """
    Create a new group with the given name (group_name)

    :param group_name: str, the name of the group to be created
    :return: AuthGroup, the newly created AuthGroup object
    """
    new_group = AuthGroup(role=group_name)
    async with async_session.begin() as session:
        session.add(new_group)
    return new_group


async def fetch_membership(group_id, user_id):
    """
    Retrieve a membership record by the group id (group_id) and user id (user_id)

    :param group_id: int, the id of the group
    :param user_id: int, the id of the user
    :return: AuthMembership, the AuthMembership object representing the membership record
    """
    query = select(AuthMembership).where(
        and_(AuthMembership.group_id == group_id, AuthMembership.user_id == user_id)
    )  # noqa: E712
    async with async_session() as session:
        res = await session.execute(query)
        # the result type of this query is a sqlalchemy CursorResult
        # .all will return a list of Rows
        ret = res.scalars().first()
        # the result of .scalars().first() is a single Library object

        return ret


async def create_membership(group_id, user_id):
    """
    Create a new membership record with the given group id (group_id) and user id (user_id)

    :param group_id: int, the id of the group
    :param user_id: int, the id of the user
    :return: AuthMembership, the newly created AuthMembership object
    """
    new_mem = AuthMembership(user_id=user_id, group_id=group_id)
    async with async_session.begin() as session:
        session.add(new_mem)
    return new_mem


# instructor_courses
# ------------------
async def fetch_instructor_courses(
    instructor_id: int, course_id: Optional[int] = None
) -> List[CourseInstructorValidator]:
    """
    Retrieve a list of courses for which the given instructor id (instructor_id) is an instructor.
    If the optional course_id value is included then return the row for that
    course to verify that instructor_id is an instructor for course_id

    :param instructor_id: int, the id of the instructor
    :param course_id: Optional[int], the id of the course (if provided)
    :return: List[CourseInstructorValidator], a list of CourseInstructorValidator objects representing the courses
    """
    query = select(CourseInstructor)
    if course_id is not None:
        query = query.where(
            and_(
                CourseInstructor.instructor == instructor_id,
                CourseInstructor.course == course_id,
            )
        )
    else:
        query = query.where(CourseInstructor.instructor == instructor_id)
    async with async_session() as session:
        res = await session.execute(query)

        course_list = [
            CourseInstructorValidator.from_orm(x) for x in res.scalars().fetchall()
        ]
        return course_list


async def fetch_course_instructors(
    course_name: Optional[str] = None,
) -> List[AuthUserValidator]:
    """
    Retrieve a list of instructors for the given course name (course_name).
    If course_name is not provided, return a list of all instructors.

    :param course_name: Optional[str], the name of the course (if provided)
    :return: List[AuthUserValidator], a list of AuthUserValidator objects representing the instructors
    """
    query = select(AuthUser).join(CourseInstructor)
    if course_name:
        course = await fetch_course(course_name)
        query = query.where(CourseInstructor.course == course.id)
    async with async_session() as session:
        res = await session.execute(query)

    instructor_list = [AuthUserValidator.from_orm(x) for x in res.scalars().fetchall()]
    return instructor_list


async def create_instructor_course_entry(iid: int, cid: int) -> CourseInstructor:
    """
    Create a new CourseInstructor entry with the given instructor id (iid) and course id (cid)
    Sanity checks to make sure that the instructor is not already associated with the course

    :param iid: int, the id of the instructor
    :param cid: int, the id of the course
    :return: CourseInstructor, the newly created CourseInstructor object
    """

    async with async_session.begin() as session:
        res = await session.execute(
            select(CourseInstructor).where(
                (CourseInstructor.course == cid) & (CourseInstructor.instructor == iid)
            )
        )
        ci = res.scalars().first()
        if ci is None:
            ci = CourseInstructor(course=cid, instructor=iid)
            session.add(ci)
    return ci


async def fetch_course_students(course_id: int) -> List[AuthUserValidator]:
    """
    Retrieve a list of students for the given course id (course_id)

    :param course_id: int, the id of the course
    :return: List[AuthUserValidator], a list of AuthUserValidator objects representing the students
    """
    query = (
        select(AuthUser)
        .join(UserCourse, UserCourse.user_id == AuthUser.id)
        .where(UserCourse.course_id == course_id)
    )
    async with async_session() as session:
        res = await session.execute(query)
    student_list = [AuthUserValidator.from_orm(x) for x in res.scalars().fetchall()]
    return student_list


# Code
# ----
async def create_code_entry(data: CodeValidator) -> CodeValidator:
    """
    Create a new code entry with the given data (data)

    :param data: CodeValidator, the CodeValidator object representing the code entry data
    :return: CodeValidator, the newly created CodeValidator object
    """
    new_code = Code(**data.dict())
    async with async_session.begin() as session:
        session.add(new_code)

    return CodeValidator.from_orm(new_code)


async def fetch_code(
    sid: str, acid: str, course_id: int, limit: int = 0
) -> List[CodeValidator]:
    """
    Retrieve a list of the most recent code entries for the given student id (sid), assignment id (acid), and course id (course_id).

    :param sid: str, the id of the student
    :param acid: str, the id of the assignment
    :param course_id: int, the id of the course
    :param limit: int, the maximum number of code entries to retrieve (0 for all)
    :return: List[CodeValidator], a list of CodeValidator objects representing the code entries
    """
    query = (
        select(Code)
        .where((Code.sid == sid) & (Code.acid == acid) & (Code.course_id == course_id))
        .order_by(Code.id.desc())
    )
    if limit > 0:
        query = query.limit(limit)
    async with async_session() as session:
        res = await session.execute(query)

        code_list = [CodeValidator.from_orm(x) for x in res.scalars().fetchall()]
        # We retrieved most recent first, but want to return results in chronological order
        code_list.reverse()
        return code_list


# Server-side grading
# -------------------
# Return the feedback associated with this question if this question should be graded on the server instead of on the client; otherwise, return None.
async def is_server_feedback(div_id: str, course: str) -> Optional[Dict[str, Any]]:
    """
    Check if server feedback is available for the given div id (div_id) and course name (course).
    If server feedback is available and login is required, return the decoded feedback.

    :param div_id: str, the id of the div element
    :param course: str, the name of the course
    :return: Optional[Dict[str, Any]], a dictionary representing the decoded feedback (if available)
    """
    # Get the information about this question.
    query = (
        select(Question, Courses)
        .where(Question.name == div_id)
        .join(Courses, Question.base_course == Courses.base_course)
        .where(Courses.course_name == course)
    )
    async with async_session() as session:
        query_results = (await session.execute(query)).first()

        # Get the feedback, if it exists.
        feedback = query_results and query_results.Question.feedback
        # If there's feedback and a login is required (necessary for server-side grading), return the decoded feedback.
        if feedback and query_results.Courses.login_required:
            return json.loads(feedback)
        # Otherwise, grade on the client.
        return None


# Development and Testing Utils
# -----------------------------


async def create_initial_courses_users():
    """
    This function populates the database with the common base courses and creates a test user.
    """
    BASE_COURSES = [
        "boguscourse",
        "ac1",
        "cppds",
        "cppforpython",
        "csawesome",
        "csjava",
        "fopp",
        "httlads",
        "java4python",
        "JS4Python",
        "learnwebgl2",
        "MasteringDatabases",
        "overview",
        "py4e-int",
        "pythonds",
        "pythonds3",
        "StudentCSP",
        "TeacherCSP",
        "thinkcpp",
        "thinkcspy",
        "webfundamentals",
        "test_course_1",
    ]

    for c in BASE_COURSES:
        new_course = CoursesValidator(
            course_name=c,
            base_course=c,
            term_start_date=datetime.date(2000, 1, 1),
            login_required=False,
            allow_pairs=False,
            downloads_enabled=False,
            courselevel="",
            institution="",
            new_server=True,
        )
        await create_course(new_course)
    # Make a user. TODO: should we not do this for production?
    await create_user(
        AuthUserValidator(
            username="testuser1",
            first_name="test",
            last_name="user",
            password="xxx",
            email="testuser1@example.com",
            course_name="overview",
            course_id=BASE_COURSES.index("overview") + 1,
            donated=True,
            active=True,
            accept_tcp=True,
            created_on=datetime.datetime(2020, 1, 1, 0, 0, 0),
            modified_on=datetime.datetime(2020, 1, 1, 0, 0, 0),
            registration_key="",
            registration_id="",
            reset_password_key="",
        )
    )


# User Progress
# -------------


async def create_user_state_entry(user_id: int, course_name: str) -> UserStateValidator:
    """
    Create a new UserState entry with the given user id (user_id) and course name (course_name)

    :param user_id: int, the id of the user
    :param course_name: str, the name of the course
    :return: UserStateValidator, the newly created UserStateValidator object
    """
    new_us = UserState(user_id=user_id, course_name=course_name)
    async with async_session.begin() as session:
        session.add(new_us)
    return UserStateValidator.from_orm(new_us)


async def update_user_state(user_data: schemas.LastPageData):
    """
    Update the UserState entry with the given user data (user_data)

    :param user_data: LastPageData, the LastPageData object representing the user data
    """
    ud = user_data.dict()
    # LastPageData contains information for both user_state and user_sub_chapter_progress tables
    # we do not need the completion flag in the user_state table
    ud.pop("completion_flag")
    rslogger.debug(f"user data = {ud}")
    stmt = (
        update(UserState)
        .where(
            (UserState.user_id == user_data.user_id)
            & (UserState.course_name == user_data.course_name)
        )
        .values(**ud)
    )
    async with async_session.begin() as session:
        await session.execute(stmt)
    rslogger.debug("SUCCESS")


async def update_sub_chapter_progress(user_data: schemas.LastPageData):
    """
    Update the UserSubChapterProgress entry with the given user data (user_data)

    :param user_data: LastPageData, the LastPageData object representing the user data
    """
    ud = user_data.dict()
    ud.pop("last_page_url")
    ud.pop("last_page_scroll_location")
    ud.pop("last_page_accessed_on")
    ud["status"] = ud.pop("completion_flag")
    ud["chapter_id"] = ud.pop("last_page_chapter")
    ud["sub_chapter_id"] = ud.pop("last_page_subchapter")
    if ud["status"] > -1:
        ud["end_date"] = canonical_utcnow()

    stmt = (
        update(UserSubChapterProgress)
        .where(
            (UserSubChapterProgress.user_id == user_data.user_id)
            & (UserSubChapterProgress.chapter_id == user_data.last_page_chapter)
            & (UserSubChapterProgress.sub_chapter_id == user_data.last_page_subchapter)
            & (
                (UserSubChapterProgress.course_name == user_data.course_name)
                | (
                    UserSubChapterProgress.course_name == None  # noqa 711
                )  # Back fill for old entries without course
            )
        )
        .values(**ud)
    )
    async with async_session.begin() as session:
        await session.execute(stmt)


async def fetch_last_page(user: AuthUserValidator, course_name: str):
    """
    Retrieve the last page accessed by the given user (user) for the given course name (course_name)

    :param user: AuthUserValidator, the AuthUserValidator object representing the user
    :param course_name: str, the name of the course
    :return: Tuple[str, str, str, str, str], a tuple representing the last page accessed
    """
    course = await fetch_course(course_name)

    query = (
        select(
            [
                UserState.last_page_url,
                UserState.last_page_hash,
                Chapter.chapter_name,
                UserState.last_page_scroll_location,
                SubChapter.sub_chapter_name,
            ]
        )
        .where(
            (UserState.user_id == user.id)
            & (UserState.last_page_chapter == Chapter.chapter_label)
            & (UserState.course_name == course.course_name)
            & (SubChapter.chapter_id == Chapter.id)
            & (UserState.last_page_subchapter == SubChapter.sub_chapter_label)
            & (Chapter.course_id == course.base_course)
        )
        .order_by(UserState.last_page_accessed_on.desc())
    )

    async with async_session() as session:
        res = await session.execute(query)
        # for A query like this one with columns from multiple tables
        # res.first() returns a tuple
        rslogger.debug(f"LP {res}")
        PageData = namedtuple("PageData", [col for col in res.keys()])  # type: ignore
        rdata = res.first()
        rslogger.debug(f"{rdata=}")
        if rdata:
            return PageData(*rdata)
        else:
            return None


async def fetch_user_sub_chapter_progress(
    user, last_page_chapter=None, last_page_subchapter=None
) -> List[UserSubChapterProgressValidator]:
    """
    Retrieve the UserSubChapterProgress entries for the given user (user) and optional chapter and subchapter.

    :param user: AuthUserValidator, the AuthUserValidator object representing the user
    :param last_page_chapter: str, the chapter label of the last page accessed (optional)
    :param last_page_subchapter: str, the subchapter label of the last page accessed (optional)
    :return: List[UserSubChapterProgressValidator], a list of UserSubChapterProgressValidator objects
    """
    where_clause = (UserSubChapterProgress.user_id == user.id) & (
        UserSubChapterProgress.course_name == user.course_name
    )

    if last_page_chapter:
        where_clause = (
            where_clause
            & (UserSubChapterProgress.chapter_id == last_page_chapter)
            & (UserSubChapterProgress.sub_chapter_id == last_page_subchapter)
        )

    query = select(UserSubChapterProgress).where(where_clause)

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [
            UserSubChapterProgressValidator.from_orm(x)
            for x in res.scalars().fetchall()
        ]


async def create_user_sub_chapter_progress_entry(
    user, last_page_chapter, last_page_subchapter, status=-1
) -> UserSubChapterProgressValidator:
    """
    Create a new UserSubChapterProgress entry with the given user (user), chapter label (last_page_chapter),
    subchapter label (last_page_subchapter), and status (status)

    :param user: AuthUserValidator, the AuthUserValidator object representing the user
    :param last_page_chapter: str, the chapter label of the last page accessed
    :param last_page_subchapter: str, the subchapter label of the last page accessed
    :param status: int, the completion status (default is -1)
    :return: UserSubChapterProgressValidator, the newly created UserSubChapterProgressValidator object
    """
    new_uspe = UserSubChapterProgress(
        user_id=user.id,
        chapter_id=last_page_chapter,
        sub_chapter_id=last_page_subchapter,
        status=status,
        start_date=canonical_utcnow(),
        course_name=user.course_name,
    )
    async with async_session.begin() as session:
        session.add(new_uspe)
    return UserSubChapterProgressValidator.from_orm(new_uspe)


async def fetch_user_chapter_progress(
    user, last_page_chapter: str
) -> UserChapterProgressValidator:
    """
    Retrieve the UserChapterProgress entry for the given user (user) and chapter label (last_page_chapter).

    :param user: AuthUserValidator, the AuthUserValidator object representing the user
    :param last_page_chapter: str, the chapter label of the last page accessed
    :return: UserChapterProgressValidator, the UserChapterProgressValidator object
    """
    query = select(UserChapterProgress).where(
        (
            UserChapterProgress.user_id == str(user.id)
        )  # TODO: this is bad! the DB has user.id as a string!
        & (UserChapterProgress.chapter_id == last_page_chapter)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return UserChapterProgressValidator.from_orm(res.scalars().first())


async def create_user_chapter_progress_entry(
    user, last_page_chapter, status
) -> UserChapterProgressValidator:
    """
    Create a new UserChapterProgress entry with the given user (user), chapter label (last_page_chapter), and status (status)

    :param user: AuthUserValidator, the AuthUserValidator object representing the user
    :param last_page_chapter: str, the chapter label of the last page accessed
    :param status: int, the completion status
    :return: UserChapterProgressValidator, the newly created UserChapterProgressValidator object
    """
    new_ucp = UserChapterProgress(
        user_id=str(user.id),
        chapter_id=last_page_chapter,
        status=status,
        start_date=canonical_utcnow(),
    )
    async with async_session.begin() as session:
        session.add(new_ucp)
    return UserChapterProgressValidator.from_orm(new_ucp)


#
# Select Question Support
# -----------------------


async def create_selected_question(
    sid: str,
    selector_id: str,
    selected_id: str,
    points: Optional[int] = None,
    competency: Optional[str] = None,
) -> SelectedQuestionValidator:
    """
    Create a new SelectedQuestion entry with the given sid, selector_id, selected_id, points, and competency.

    :param sid: str, the student id
    :param selector_id: str, the id of the question selector
    :param selected_id: str, the id of the selected question
    :param points: int, the points earned (optional)
    :param competency: str, the competency (optional)
    :return: SelectedQuestionValidator, the newly created SelectedQuestionValidator object
    """
    new_sqv = SelectedQuestion(
        sid=sid,
        selector_id=selector_id,
        selected_id=selected_id,
        points=points,
        competency=competency,
    )
    async with async_session.begin() as session:
        session.add(new_sqv)
    return SelectedQuestionValidator.from_orm(new_sqv)


async def fetch_selected_question(
    sid: str, selector_id: str
) -> SelectedQuestionValidator:
    """
    Retrieve the SelectedQuestion entry for the given sid and selector_id.

    :param sid: str, the student id
    :param selector_id: str, the id of the question selector
    :return: SelectedQuestionValidator, the SelectedQuestionValidator object
    """
    query = select(SelectedQuestion).where(
        (SelectedQuestion.sid == sid) & (SelectedQuestion.selector_id == selector_id)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return SelectedQuestionValidator.from_orm(res.scalars().first())


async def update_selected_question(sid: str, selector_id: str, selected_id: str):
    """
    Update the selected_id of the SelectedQuestion entry for the given sid and selector_id.

    :param sid: str, the student id
    :param selector_id: str, the id of the question selector
    :param selected_id: str, the id of the selected question
    """
    stmt = (
        update(SelectedQuestion)
        .where(
            (SelectedQuestion.sid == sid)
            & (SelectedQuestion.selector_id == selector_id)
        )
        .values(selected_id=selected_id)
    )
    async with async_session.begin() as session:
        await session.execute(stmt)
    rslogger.debug("SUCCESS")


# Questions and Assignments
# -------------------------


# write a function that fetches all Assignment objects given a course name
async def fetch_assignments(
    course_name: str,
    is_peer: Optional[bool] = False,
    is_visible: Optional[bool] = False,
    fetch_all: Optional[bool] = False,
) -> List[AssignmentValidator]:
    """
    Fetch all Assignment objects for the given course name.
    If is_peer is True then only select asssigments for peer isntruction.
    If is_visible is True then only fetch visible assignments.

    :param course_name: str, the course name
    :param is_peer: bool, whether or not the assignment is a peer assignment
    :return: List[AssignmentValidator], a list of AssignmentValidator objects
    """
    if is_visible:
        vclause = Assignment.visible == is_visible
    else:
        vclause = True

    if is_peer:
        pclause = Assignment.is_peer == True  # noqa: E712
    else:
        pclause = or_(
            Assignment.is_peer == False, Assignment.is_peer == None  # noqa: E712, E711
        )

    if fetch_all:
        pclause = True
        vclause = True

    query = (
        select(Assignment)
        .where(
            and_(
                Assignment.course == Courses.id,
                Courses.course_name == course_name,
                vclause,
                pclause,
            )
        )
        .order_by(Assignment.duedate.desc())
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [AssignmentValidator.from_orm(a) for a in res.scalars()]


# write a function that fetches all Assignment objects given a course name
async def fetch_one_assignment(assignment_id: int) -> AssignmentValidator:
    """
    Fetch one Assignment object with calculated total points for related exercises.

    :param assignment_id: int, the assignment id

    :return: AssignmentValidator
    """
    async with async_session() as session:
        assignment_query = select(Assignment).where(Assignment.id == assignment_id)
        assignment_result = await session.execute(assignment_query)
        assignment = assignment_result.scalar_one_or_none()

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assignment with id {assignment_id} not found",
            )

        exercises_query = select(AssignmentQuestion).where(
            AssignmentQuestion.assignment_id == assignment_id
        )
        exercises_result = await session.execute(exercises_query)
        exercises = exercises_result.scalars().all()

        total_points = sum(exercise.points for exercise in exercises)

        assignment.points = total_points
        session.add(assignment)
        await session.commit()

        return AssignmentValidator.from_orm(assignment)


async def create_assignment(assignment: AssignmentValidator) -> AssignmentValidator:
    """
    Create a new Assignment object with the given data (assignment)

    :param assignment: AssignmentValidator, the AssignmentValidator object representing the assignment data
    :return: AssignmentValidator, the newly created AssignmentValidator object
    """
    new_assignment = Assignment(**assignment.dict())
    async with async_session.begin() as session:
        session.add(new_assignment)

    return AssignmentValidator.from_orm(new_assignment)


async def update_assignment(assignment: AssignmentValidator) -> None:
    """
    Update an Assignment object with the given data (assignment)
    """
    assignment_updates = assignment.dict()
    assignment_updates["current_index"] = 0
    del assignment_updates["id"]

    stmt = (
        update(Assignment)
        .where(Assignment.id == assignment.id)
        .values(assignment_updates)
    )
    async with async_session.begin() as session:
        await session.execute(stmt)


async def create_assignment_question(
    assignmentQuestion: AssignmentQuestionValidator,
) -> AssignmentQuestionValidator:
    """
    Create a new AssignmentQuestion object with the given data (assignmentQuestion)

    :param assignmentQuestion: AssignmentQuestionValidator, the AssignmentQuestionValidator object representing the assignment question data
    :return: AssignmentQuestionValidator, the newly created AssignmentQuestionValidator object
    """
    new_assignment_question = AssignmentQuestion(**assignmentQuestion.dict())
    async with async_session.begin() as session:
        session.add(new_assignment_question)

    return AssignmentQuestionValidator.from_orm(new_assignment_question)


async def update_multiple_assignment_questions(
    exercises: List[AssignmentQuestionUpdateDict],
) -> list[AssignmentQuestionValidator]:
    """
    Update multiple AssignmentQuestion objects with the given data (exercises).
    Also updates the Question table for fields like question_json, htmlsrc, chapter, subchapter,
    author, autograde, topic, feedback, name, difficulty, and tags if the user is the owner.

    :param exercises: List of dictionaries with fields from both AssignmentQuestionValidator and QuestionValidator
    :return: List of updated AssignmentQuestionValidator objects
    """

    def is_valid_option(option, question_type, options_enum):
        """
        Check if the given option is valid for the specified question type.

        :param option: Option to validate (e.g., which_to_grade or autograde).
        :param question_type: QuestionType of the exercise.
        :param options_enum: Enum class (e.g., WhichToGradeOptions or AutogradeOptions).
        :return: True if valid, False otherwise.
        """
        for enum_option in options_enum:
            if enum_option.value[0] == option:
                supported_types = [qt.value_only() for qt in enum_option.value[2]]
                return question_type in supported_types
        return False

    async with async_session.begin() as session:
        updated_questions = []

        # Preload all necessary data to minimize database queries
        exercise_ids = [exercise.get("id") for exercise in exercises]
        question_ids = [exercise.get("question_id") for exercise in exercises]

        existing_questions_query = select(AssignmentQuestion).where(
            AssignmentQuestion.id.in_(exercise_ids)
        )
        existing_questions_result = await session.execute(existing_questions_query)
        existing_questions = {q.id: q for q in existing_questions_result.scalars()}

        questions_query = select(Question).where(Question.id.in_(question_ids))
        questions_result = await session.execute(questions_query)
        questions = {q.id: q for q in questions_result.scalars()}

        for exercise in exercises:
            existing_question = existing_questions.get(exercise.get("id"))

            if not existing_question:
                continue

            question = questions.get(exercise.get("question_id"))

            if not question:
                continue

            question_type = (
                question.question_type
            )  # Access question_type from the related question

            exercise_dict = exercise.copy()

            # Validate and update which_to_grade
            if not is_valid_option(
                exercise_dict.get("which_to_grade"), question_type, WhichToGradeOptions
            ):
                exercise_dict["which_to_grade"] = existing_question.which_to_grade

            # Validate and update autograde
            if not is_valid_option(
                exercise_dict.get("autograde"), question_type, AutogradeOptions
            ):
                exercise_dict["autograde"] = existing_question.autograde

            # Extract AssignmentQuestion fields and exclude Question fields
            aq_fields = {
                k: v
                for k, v in exercise_dict.items()
                if k in AssignmentQuestionValidator.__annotations__
            }

            # Update the existing question with validated data
            for field, value in aq_fields.items():
                setattr(existing_question, field, value)

            # Add the updated question to the session
            session.add(existing_question)

            # Update the Question table if the user is the owner
            if exercise.get("owner") == question.owner:
                question_updates = {}

                # List of fields to check and update in the Question table
                editable_fields = [
                    "question_json",
                    "htmlsrc",
                    "chapter",
                    "subchapter",
                    "author",
                    "autograde",
                    "topic",
                    "feedback",
                    "name",
                    "difficulty",
                    "tags",
                ]

                # Check if any of the editable fields have changed
                for field in editable_fields:
                    if (
                        field in exercise_dict
                        and exercise_dict[field] is not None
                        and exercise_dict[field] != getattr(question, field, None)
                    ):
                        question_updates[field] = exercise_dict[field]

                # If there are updates to apply to the Question table
                if question_updates:
                    # Update the Question record
                    for field, value in question_updates.items():
                        setattr(question, field, value)

                    # Add the updated question to the session
                    session.add(question)

            updated_questions.append(
                AssignmentQuestionValidator.from_orm(existing_question)
            )

        await session.commit()

    return updated_questions


async def update_assignment_question(
    assignmentQuestion: AssignmentQuestionValidator,
) -> AssignmentQuestionValidator:
    """
    Update an AssignmentQuestion object with the given data (assignmentQuestion)
    """
    new_assignment_question = AssignmentQuestion(**assignmentQuestion.dict())
    async with async_session.begin() as session:
        await session.merge(new_assignment_question)

    return AssignmentQuestionValidator.from_orm(new_assignment_question)


async def update_assignment_exercises(
    payload: schemas.UpdateAssignmentExercisesPayload,
):
    async with async_session() as session:
        # Step 1: Get the current assignment data
        assignment_query = select(Assignment).where(
            Assignment.id == payload.assignmentId
        )
        assignment_result = await session.execute(assignment_query)
        assignment = assignment_result.scalar_one_or_none()

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assignment with id {payload.assignmentId} not found",
            )

        # Step 2: Get the maximum sorting_priority considering isReading
        query_max_priority = select(
            func.max(AssignmentQuestion.sorting_priority)
        ).where(
            AssignmentQuestion.assignment_id == payload.assignmentId,
            AssignmentQuestion.reading_assignment == payload.isReading,
        )
        max_priority_result = await session.execute(query_max_priority)
        max_sort_priority = (
            max_priority_result.scalar() or 0
        )  # If there are no records, start from 0

        points_to_add = 0
        points_to_remove = 0
        new_questions = []

        # Step 3: Create new records in AssignmentQuestion for idsToAdd (if any)
        if payload.idsToAdd:
            for i, question_id in enumerate(payload.idsToAdd, start=1):
                # Assume we have a way to get the points for the question
                question_points_query = select(Question.difficulty).where(
                    Question.id == question_id
                )
                question_points_result = await session.execute(question_points_query)
                question_points = (
                    question_points_result.scalar() or 1
                )  # If the question is not found, 1 point

                new_question = AssignmentQuestion(
                    assignment_id=payload.assignmentId,
                    question_id=question_id,
                    points=question_points,  # Use the points from the question
                    timed=None,  # Leave as null
                    autograde=(
                        "interaction" if payload.isReading else "pct_correct"
                    ),  # Depends on isReading
                    which_to_grade="best_answer",
                    reading_assignment=payload.isReading,
                    sorting_priority=max_sort_priority
                    + i,  # Increment from max_sort_priority
                    activities_required=None,
                )
                new_questions.append(new_question)
                points_to_add += question_points  # Increase by the number of points

            # Add new records to the session
            session.add_all(new_questions)

        # Step 4: Remove records for idsToRemove (if any)
        if payload.idsToRemove:
            query_remove = select(AssignmentQuestion).where(
                AssignmentQuestion.assignment_id == payload.assignmentId,
                AssignmentQuestion.id.in_(payload.idsToRemove),
            )
            remove_result = await session.execute(query_remove)
            questions_to_remove = remove_result.scalars().all()

            # Calculate the total points for the questions to be removed
            for question in questions_to_remove:
                points_to_remove += question.points

            # Remove records
            query_delete = delete(AssignmentQuestion).where(
                AssignmentQuestion.assignment_id == payload.assignmentId,
                AssignmentQuestion.id.in_(payload.idsToRemove),
            )
            await session.execute(query_delete)

        # Step 5: Update points in Assignment
        assignment.points += points_to_add - points_to_remove
        session.add(assignment)

        # Step 6: Apply changes
        await session.commit()

        # Log the result
        rslogger.debug(f"Added questions: {new_questions}")
        rslogger.debug(f"Removed questions: {points_to_remove}")
        return {
            "added": len(payload.idsToAdd) if payload.idsToAdd else 0,
            "removed": len(payload.idsToRemove) if payload.idsToRemove else 0,
            "total_points": assignment.points,
        }


async def reorder_assignment_questions(question_ids: List[int]):
    """
    Reorder the assignment questions with the given question ids (question_ids)
    """
    async with async_session.begin() as session:
        for i, qid in enumerate(question_ids):
            d = dict(sorting_priority=i)
            stmt = (
                update(AssignmentQuestion)
                .where(AssignmentQuestion.id == qid)
                .values(**d)
            )
            await session.execute(stmt)


async def remove_assignment_questions(assignment_ids: List[int]):
    """
    Remove all assignment questions for the given assignment ids (assignment_ids)
    """
    stmt = delete(AssignmentQuestion).where(AssignmentQuestion.id.in_(assignment_ids))
    async with async_session.begin() as session:
        await session.execute(stmt)


async def fetch_problem_data(assignment_id: int, course_name: str) -> list:
    """
    Fetch problem data for a given assignment.

    :param assignment_id: int, the id of the assignment
    :return: list, a list of tuples containing timestamp, name, sid, event, and act
    """
    query = (
        select(
            Useinfo.timestamp.label("ts"),
            Question.name,
            Useinfo.sid,
            Useinfo.event,
            Useinfo.act,
        )
        .join(AssignmentQuestion, AssignmentQuestion.question_id == Question.id)
        .join(Useinfo, Question.name == Useinfo.div_id)
        .where(
            and_(AssignmentQuestion.assignment_id == assignment_id),
            (Useinfo.course_id == course_name),
        )
        .order_by(Useinfo.sid, Useinfo.timestamp)
    )

    async with async_session() as session:
        result = await session.execute(query)
        return result.all()


async def fetch_reading_assignment_data(assignment_id: int, sid: str) -> list:
    """
    Fetch reading assignment data for a given assignment and student id.

    :param assignment_id: int, the id of the assignment
    :param sid: str, the student id
    :return: list[AssignmentQuestionValidator], a list of AssignmentQuestionValidator objects
    """

    pass


async def fetch_all_assignment_stats(
    course_name: str, userid: int
) -> list[GradeValidator]:
    """
    Fetch the Grade information for all assignments for a given student in a given course.

    :param course_name: The name of the current course
    :type course_name: str
    :param userid: the users numeric id
    :type userid: int
    :return list[AssignmentValidator]: a list of AssignmentValidator objects
    """
    query = select(Grade).where(
        and_(
            Assignment.course == Courses.id,
            Courses.course_name == course_name,
            Grade.assignment == Assignment.id,
            Grade.auth_user == userid,
        )
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [GradeValidator.from_orm(a) for a in res.scalars()]


async def fetch_all_grades_for_assignment(
    assignment_id: int,
) -> list[GradeValidator]:
    """
    Fetch all grades for the given assignment id (assignment_id)

    :param assignment_id: int, the id of the assignment
    :return: List[GradeValidator], a list of GradeValidator objects
    """
    query = select(Grade).where(Grade.assignment == assignment_id)

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [GradeValidator.from_orm(a) for a in res.scalars()]


async def fetch_grade(userid: int, assignmentid: int) -> Optional[GradeValidator]:
    """
    Fetch the Grade object for the given user and assignment.

    :param userid: int, the user id
    :param assignmentid: int, the assignment id
    :return: Optional[GradeValidator], the GradeValidator object
    """
    query = select(Grade).where(
        and_(
            Grade.auth_user == userid,
            Grade.assignment == assignmentid,
        )
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return GradeValidator.from_orm(res.scalars().first())


# write a function that given a GradeValidator object inserts a new Grade object into the database
# or updates an existing one
#
# This function should return the GradeValidator object that was inserted or updated
async def upsert_grade(grade: GradeValidator) -> GradeValidator:
    """
    Insert a new Grade object into the database or update an existing one.

    :param grade: GradeValidator, the GradeValidator object
    :return: GradeValidator, the GradeValidator object
    """
    new_grade = Grade(**grade.dict())
    success = True
    try:
        async with async_session.begin() as session:
            # merge either inserts or updates the object
            await session.merge(new_grade)
    except (IntegrityError, UniqueViolationError) as e:
        rslogger.error(f"IntegrityError: {e} id = {new_grade.id}")
        success = False
    if success:
        return GradeValidator.from_orm(new_grade)
    else:
        return await fetch_grade(grade.auth_user, grade.assignment)


async def fetch_question(
    name: str, basecourse: Optional[str] = None, assignment: Optional[str] = None
) -> QuestionValidator:
    """
    Fetch a single matching question row from the database that matches
    the name (div_id) of the question.  If the base course is provided
    make sure the question comes from that basecourse. basecourse,name pairs
    are guaranteed to be unique in the questions table

    More and more questions have globally unique names in the runestone
    database and that is definitely a direction to keep pushing.  But
    it is possible that there are duplicates but we are not going to
    worry about that we are just going to return the first one we find.

    :param name: str, the name (div_id) of the question
    :param basecourse: str, the base course (optional)
    :param assignment: str, the assignment (optional)
    :return: QuestionValidator, the QuestionValidator object
    """
    where_clause = Question.name == name
    if basecourse:
        where_clause = where_clause & (Question.base_course == basecourse)

    query = select(Question).where(where_clause)

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return QuestionValidator.from_orm(res.scalars().first())


async def count_matching_questions(name: str) -> int:
    """
    Count the number of Question entries that match the given name.

    :param name: str, the name (div_id) of the question
    :return: int, the number of matching questions
    """
    query = select(func.count(Question.name)).where(Question.name == name)

    async with async_session() as session:
        res = await session.execute(query)
        return res.scalars().first()


auto_gradable_q = [
    "clickablearea",
    "mchoice",
    "parsonsprob",
    "dragndrop",
    "fillintheblank",
    "lp",
]


async def fetch_matching_questions(request_data: schemas.SelectQRequest) -> List[str]:
    """
    Return a list of question names (div_ids) that match the criteria
    for a particular question. This is used by select questions and in
    particular `get_question_source`
    """
    if request_data.questions:
        questionlist = request_data.questions.split(",")
        questionlist = [q.strip() for q in questionlist]
    elif request_data.proficiency:
        prof = request_data.proficiency.strip()
        rslogger.debug(prof)
        where_clause = (Competency.competency == prof) & (
            Competency.question == Question.id
        )
        if request_data.primary:
            where_clause = where_clause & (Competency.is_primary == True)  # noqa E712
        if request_data.min_difficulty:
            where_clause = where_clause & (
                Question.difficulty >= float(request_data.min_difficulty)
            )
        if request_data.max_difficulty:
            where_clause = where_clause & (
                Question.difficulty <= float(request_data.max_difficulty)
            )
        if request_data.autogradable:
            where_clause = where_clause & (
                (Question.autograde == "unittest")
                | Question.question_type.in_(auto_gradable_q)
            )
        if request_data.limitBaseCourse:
            where_clause = where_clause & (
                Question.base_course == request_data.limitBaseCourse
            )
        query = select(Question.name).where(where_clause)

        async with async_session() as session:
            res = await session.execute(query)
            rslogger.debug(f"{res=}")
            questionlist = []
            for row in res:
                questionlist.append(row[0])

    return questionlist


async def fetch_questions_by_search_criteria(
    criteria: schemas.SearchSpecification,
) -> List[QuestionValidator]:
    """
    Fetch a list of questions that match the search criteria
    regular expression matches are case insensitive

    :param search: str, the search string
    :return: List[QuestionValidator], a list of QuestionValidator objects
    """
    where_criteria = []
    if criteria.source_regex:
        where_criteria.append(
            or_(
                Question.question.regexp_match(criteria.source_regex, flags="i"),
                Question.htmlsrc.regexp_match(criteria.source_regex, flags="i"),
                Question.topic.regexp_match(criteria.source_regex, flags="i"),
                Question.name.regexp_match(criteria.source_regex, flags="i"),
            )
        )

    if criteria.question_type:
        where_criteria.append(Question.question_type == criteria.question_type)

    if criteria.author:
        where_criteria.append(Question.author.regexp_match(criteria.author, flags="i"))

    if criteria.base_course:
        where_criteria.append(Question.base_course == criteria.base_course)

    if len(where_criteria) == 0:
        raise ValueError("No search criteria provided")

    # todo: add support for tags
    query = select(Question).where(and_(*where_criteria))
    rslogger.debug(f"{query=}")
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [QuestionValidator.from_orm(q) for q in res.scalars().fetchall()]


async def search_exercises(
    criteria: schemas.ExercisesSearchRequest,
) -> dict:
    """
    Smart search for exercises with pagination, filtering, and sorting.

    :param criteria: Search parameters including filters, pagination, and sorting
    :return: Dictionary with search results and pagination metadata
    """
    # Base query
    query = select(Question).where(Question.question_type != "page")

    # If assignment_id is provided, exclude already attached exercises
    if criteria.assignment_id is not None:
        assigned_questions = (
            select(AssignmentQuestion.question_id)
            .where(AssignmentQuestion.assignment_id == criteria.assignment_id)
            .scalar_subquery()
        )
        query = query.where(Question.id.not_in(assigned_questions))

    # Process filters
    if criteria.filters:
        for field, filter_data in criteria.filters.items():
            if not filter_data:
                continue

            # Get filter value and mode
            filter_value = filter_data.get("value")
            filter_mode = filter_data.get("matchMode", "contains")

            # Skip empty filter values
            if filter_value is None or filter_value == "":
                continue

            # Process global search (search in multiple fields)
            if field == "global":
                search_fields = ["name", "author", "topic", "tags"]
                or_conditions = []

                for search_field in search_fields:
                    if hasattr(Question, search_field):
                        column = getattr(Question, search_field)
                        or_conditions.append(column.ilike(f"%{filter_value}%"))

                if or_conditions:
                    query = query.where(or_(*or_conditions))

            # Process specific field filters
            elif hasattr(Question, field):
                column = getattr(Question, field)

                if filter_value is not None:
                    if filter_mode == "contains":
                        query = query.where(column.ilike(f"%{filter_value}%"))
                    elif filter_mode == "equals":
                        query = query.where(column == filter_value)
                    elif filter_mode == "startsWith":
                        query = query.where(column.ilike(f"{filter_value}%"))
                    elif filter_mode == "endsWith":
                        query = query.where(column.ilike(f"%{filter_value}"))
                    elif filter_mode == "notContains":
                        query = query.where(not_(column.ilike(f"%{filter_value}%")))
                    elif filter_mode == "notEquals":
                        query = query.where(column != filter_value)
                    elif (
                        filter_mode == "in"
                        and isinstance(filter_value, list)
                        and len(filter_value) > 0
                    ):
                        query = query.where(column.in_(filter_value))

    # Apply sorting
    if criteria.sorting and criteria.sorting.get("field"):
        field = criteria.sorting["field"]
        order = criteria.sorting.get("order", 1)  # 1 for ascending, -1 for descending

        if hasattr(Question, field):
            column = getattr(Question, field)
            query = query.order_by(asc(column) if order == 1 else desc(column))

    # Count total results (before pagination)
    count_query = select(func.count()).select_from(query.subquery())

    # Apply pagination
    query = query.offset(criteria.page * criteria.limit).limit(criteria.limit)

    # Execute queries
    async with async_session() as session:
        total_count = (await session.execute(count_query)).scalar()
        result = await session.execute(query)
        exercises = [
            QuestionValidator.from_orm(row) for row in result.scalars().fetchall()
        ]

        return {
            "exercises": exercises,
            "pagination": {
                "total": total_count,
                "page": criteria.page,
                "limit": criteria.limit,
                "pages": (total_count + criteria.limit - 1) // criteria.limit,
            },
        }


async def fetch_assignment_question(
    assignment_name: str, question_name: str
) -> AssignmentQuestionValidator:
    """
    Retrieve the AssignmentQuestion entry for the given assignment_name and question_name.

    :param assignment_name: str, the name of the assignment
    :param question_name: str, the name (div_id) of the question
    :return: AssignmentQuestionValidator, the AssignmentQuestionValidator object
    """
    query = select(AssignmentQuestion).where(
        (Assignment.name == assignment_name)
        & (Assignment.id == AssignmentQuestion.assignment_id)
        & (AssignmentQuestion.question_id == Question.id)
        & (Question.name == question_name)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return AssignmentQuestionValidator.from_orm(res.scalars().first())


async def fetch_assignment_questions(
    assignment_id: int,
) -> List[Tuple[Question, AssignmentQuestion]]:
    """
    Retrieve the AssignmentQuestion entry for the given assignment_name and question_name.

    :param assignment_name: str, the name of the assignment
    :param question_name: str, the name (div_id) of the question
    :return: AssignmentQuestionValidator, the AssignmentQuestionValidator object
    """
    query = (
        select(Question, AssignmentQuestion)
        .join(Question, AssignmentQuestion.question_id == Question.id)
        .where(AssignmentQuestion.assignment_id == assignment_id)
        .order_by(AssignmentQuestion.sorting_priority)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        # we cannot return res.scalars() because we want both objects in the row.
        # and the scalars() method onnly returns the first object in the row.
        return res


async def fetch_question_count_per_subchapter(
    course_name: str,
) -> Dict[Dict[str, str], int]:
    """
    Return a dictionary of subchapter_id: count of questions in that subchapter
    """
    query = (
        select(
            Question.chapter,
            Question.subchapter,
            func.count(Question.id).label("question_count"),
        )
        .where(
            and_(
                Question.base_course == course_name,
                Question.from_source == True,  # noqa 711
                Question.optional != True,  # noqa 711
            )
        )
        .group_by(Question.chapter, Question.subchapter)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")

    resd = {}
    for row in res:
        if row[0] not in resd:
            resd[row[0]] = {}
        resd[row[0]][row[1]] = row[2]
    return resd


async def fetch_question_grade(sid: str, course_name: str, qid: str):
    """
    Retrieve the QuestionGrade entry for the given sid, course_name, and qid.

    :param sid: str, the student id
    :param course_name: str, the course name
    :param qid: str, the question id (div_id)
    :return: QuestionGradeValidator, the QuestionGradeValidator object
    """
    query = (
        select(QuestionGrade)
        .where(
            (QuestionGrade.sid == sid)
            & (QuestionGrade.course_name == course_name)
            & (QuestionGrade.div_id == qid)
        )
        .order_by(
            QuestionGrade.id.desc(),
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        return QuestionGradeValidator.from_orm(res.scalars().one_or_none())


async def create_question_grade_entry(
    sid: str, course_name: str, qid: str, grade: int
) -> QuestionGradeValidator:
    """
    Create a new QuestionGrade entry with the given sid, course_name, qid, and grade.
    """
    new_qg = QuestionGrade(
        sid=sid,
        course_name=course_name,
        div_id=qid,
        score=grade,
        comment="autograded",
    )
    try:
        async with async_session.begin() as session:
            session.add(new_qg)
    except (IntegrityError, UniqueViolationError) as e:
        rslogger.error(f"IntegrityError: {e} id = {new_qg.id}")
        return None
    return QuestionGradeValidator.from_orm(new_qg)


async def update_question_grade_entry(
    sid: str, course_name: str, qid: str, grade: int, qge_id: Optional[int] = None
) -> QuestionGradeValidator:
    """
    Create a new QuestionGrade entry with the given sid, course_name, qid, and grade.
    """
    new_qg = QuestionGrade(
        sid=sid,
        course_name=course_name,
        div_id=qid,
        score=grade,
        comment="autograded",
    )
    if qge_id is not None:
        new_qg.id = qge_id

    async with async_session.begin() as session:
        await session.merge(new_qg)
    return QuestionGradeValidator.from_orm(new_qg)


async def fetch_user_experiment(sid: str, ab_name: str) -> int:
    """
    When a question is part of an AB experiement (ab_name) get the experiment
    group for a particular student (sid).  The group number will have
    been randomly assigned by the initial question selection.

    This number indicates whether the student will see the 1st or 2nd
    question in the question list.

    :param sid: str, the student id
    :param ab_name: str, the name of the AB experiment
    :return: int, the experiment group number
    """
    query = (
        select(UserExperiment.exp_group)
        .where((UserExperiment.sid == sid) & (UserExperiment.experiment_id == ab_name))
        .order_by(UserExperiment.id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        r = res.scalars().first()
        rslogger.debug(f"{r=}")
        return r


async def create_user_experiment_entry(
    sid: str, ab: str, group: int
) -> UserExperimentValidator:
    """
    Create a new UserExperiment entry with the given sid, ab, and group.

    :param sid: str, the student id
    :param ab: str, the name of the AB experiment
    :param group: int, the experiment group number
    :return: UserExperimentValidator, the UserExperimentValidator object
    """
    new_ue = UserExperiment(sid=sid, exp_group=group, experiment_id=ab)
    async with async_session.begin() as session:
        session.add(new_ue)
    return UserExperimentValidator.from_orm(new_ue)


async def fetch_viewed_questions(sid: str, questionlist: List[str]) -> List[str]:
    """
    Retrieve a list of questions from the given questionlist that a student (sid)
    has viewed before. Used for the selectquestion `get_question_source` to filter
    out questions that a student has seen before. One criteria of a select question
    is to make sure that a student has never seen a question before.

    The best approximation we have for that is that they will have clicked on the
    run button for that question. Of course, they may have seen the question but not
    run it, but this is the best we can do.

    :param sid: str, the student id
    :param questionlist: List[str], a list of question ids (div_id)
    :return: List[str], a list of question ids from the given questionlist that the
             student has viewed before
    """
    query = select(Useinfo).where(
        (Useinfo.sid == sid) & (Useinfo.div_id.in_(questionlist))
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        rlist = [row.div_id for row in res]
    return rlist


async def fetch_previous_selections(sid) -> List[str]:
    """
    Retrieve a list of selected question ids for the given student id (sid).

    :param sid: str, the student id
    :return: List[str], a list of selected question ids
    """
    query = select(SelectedQuestion).where(SelectedQuestion.sid == sid)
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [row.selected_id for row in res.scalars().fetchall()]


async def fetch_timed_exam(
    sid: str, exam_id: str, course_name: str
) -> TimedExamValidator:
    """
    Retrieve the TimedExam entry for the given sid, exam_id, and course_name.

    :param sid: str, the student id
    :param exam_id: str, the id of the timed exam
    :param course_name: str, the name of the course
    :return: TimedExamValidator, the TimedExamValidator object
    """
    query = (
        select(TimedExam)
        .where(
            (TimedExam.div_id == exam_id)
            & (TimedExam.sid == sid)
            & (TimedExam.course_name == course_name)
        )
        .order_by(TimedExam.id.desc())
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return TimedExamValidator.from_orm(res.scalars().first())


async def did_start_timed(sid: str, exam_id: str, course_name: str) -> bool:
    """
    Retrieve the start time for the given sid, exam_id, and course_name.

    :param sid: str, the student id
    :param exam_id: str, the id of the timed exam
    :param course_name: str, the name of the course
    :return: bool, whether the exam has started
    """
    start_query = select(Useinfo).where(
        and_(
            Useinfo.sid == sid,
            Useinfo.div_id == exam_id,
            Useinfo.course_id == course_name,
            Useinfo.event == "timedExam",
            Useinfo.act == "start",
        )
    )
    async with async_session() as session:
        start = await session.execute(start_query)
        return start.scalars().first() is not None


async def create_timed_exam_entry(
    sid: str, exam_id: str, course_name: str, start_time: datetime
) -> TimedExamValidator:
    """
    Create a new TimedExam entry with the given sid, exam_id, course_name, and start_time.

    :param sid: str, the student id
    :param exam_id: str, the id of the timed exam
    :param course_name: str, the name of the course
    :param start_time: datetime, the start time of the exam
    :return: TimedExamValidator, the TimedExamValidator object
    """
    new_te = TimedExam(
        div_id=exam_id,
        sid=sid,
        course_name=course_name,
        start_time=start_time,
        correct=0,
        incorrect=0,
        skipped=0,
    )
    async with async_session.begin() as session:
        session.add(new_te)
    return TimedExamValidator.from_orm(new_te)


async def fetch_subchapters(course, chap):
    """
    Retrieve all subchapters for a given chapter.

    :param course: str, the name of the course
    :param chap: str, the label of the chapter
    :return: ResultProxy, the result of the query
    """
    # Note: we are joining two tables so this query will not result in an defined in schemas.py
    # instead it will simply produce a bunch of tuples with the columns in the order given in the
    # select statement.
    query = (
        select(SubChapter.sub_chapter_label, SubChapter.sub_chapter_name)
        .where(
            (Chapter.id == SubChapter.chapter_id)
            & (Chapter.course_id == course)
            & (Chapter.chapter_label == chap)
        )
        .order_by(SubChapter.sub_chapter_num)
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        # **Note** with this kind of query you do NOT want to call ``.scalars()`` on the result
        return res


async def create_traceback(exc: Exception, request: Request, host: str):
    """
    Create a new TraceBack entry with the given Exception, Request, and host.

    :param exc: Exception, the exception that occurred
    :param request: Request, the request object
    :param host: str, the hostname
    """
    async with async_session.begin() as session:
        tbtext = "".join(traceback.format_tb(exc.__traceback__))
        # walk the stack trace and collect local variables into a dictionary
        curr = exc.__traceback__
        dl = []
        while curr is not None:
            frame = curr.tb_frame
            name = frame.f_code.co_name
            local_vars = frame.f_locals
            dl.append(dict(name=name, local_vars=local_vars))
            curr = curr.tb_next
        rslogger.debug(f"{dl[-2:]=}")

        new_entry = TraceBack(
            traceback=tbtext + "\n".join(textwrap.wrap(str(dl[-2:]), 80)),
            timestamp=canonical_utcnow(),
            err_message=str(exc)[:512],
            path=request.url.path[:1024],
            query_string=str(request.query_params)[:512],
            hash=hashlib.md5(tbtext.encode("utf8")).hexdigest(),
            hostname=host,
        )
        session.add(new_entry)


async def fetch_library_books():
    """
    Retrieve a list of visible library books ordered by shelf section and title.

    :return: List[LibraryValidator], a list of LibraryValidator objects
    """
    query = (
        select(Library)
        .where(Library.is_visible == True)  # noqa: E712
        .order_by(Library.shelf_section, Library.title)
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        book_list = [LibraryValidator.from_orm(x) for x in res.scalars().fetchall()]
        return book_list


async def fetch_library_book(book):
    """
    Retrieve the Library entry for the given book.

    :param book: str, the name of the book
    :return: Library, the Library object
    """
    query = select(Library).where(Library.basecourse == book)  # noqa: E712
    async with async_session() as session:
        res = await session.execute(query)
        # the result type of this query is a sqlalchemy CursorResult
        # .all will return a list of Rows
        ret = res.scalars().first()
        # the result of .scalars().first() is a single Library object

        return ret


async def update_library_book(bookid: int, vals: dict):
    """
    Update the Library entry with the given bookid and values.

    :param bookid: int, the id of the book
    :param vals: dict, a dictionary of values to update
    """

    stmt = update(Library).where(Library.id == bookid).values(**vals)
    async with async_session.begin() as session:
        await session.execute(stmt)


# TODO finish this use bookid as title temporarily
async def create_library_book(bookid: str, vals: Dict[str, Any]) -> None:
    """
    Creates a new Library object using the provided parameters and saves it in the database.

    :param bookid: str, the unique identifier of the book
    :param vals: Dict[str, Any], the dictionary containing the properties of the book
    :return: None
    """
    new_book = Library(**vals, basecourse=bookid)
    async with async_session.begin() as session:
        session.add(new_book)


async def create_book_author(author: str, document_id: str) -> None:
    """
    Creates a new BookAuthor object using the provided parameters and saves it in the database.

    :param author: str, the name of the author
    :param document_id: str, the unique identifier of the book
    :return: None
    """
    new_ba = BookAuthor(author=author, book=document_id)
    async with async_session.begin() as session:
        session.add(new_ba)


async def fetch_books_by_author(author: str) -> List[Tuple[Library, BookAuthor]]:
    """
    Fetches all books written by a given author.

    :param author: The name of the author.
    :type author: str
    :return: A list of tuples, each containing a Library and a BookAuthor object.
    :rtype: list[tuple[Library, BookAuthor]]
    """
    query = (
        select(Library, BookAuthor)
        .join(BookAuthor, BookAuthor.book == Library.basecourse)
        .where(BookAuthor.author == author)
        .order_by(BookAuthor.book)
    )
    async with async_session() as sess:
        res = await sess.execute(query)
        return res.fetchall()


async def fetch_course_practice(course_name: str) -> Optional[CoursePractice]:
    """
    Fetches the course practice row for a given course.

    :param course_name: The name of the course.
    :type course_name: str
    :return: The CoursePractice object containing the configuration of the practice feature for the given course.
    :rtype: Optional[CoursePractice]
    """
    query = (
        select(CoursePractice)
        .where(CoursePractice.course_name == course_name)
        .order_by(CoursePractice.id.desc())
    )
    async with async_session() as session:
        res = await session.execute(query)
        return res.scalars().first()


async def fetch_one_user_topic_practice(
    user: AuthUserValidator,
    last_page_chapter: str,
    last_page_subchapter: str,
) -> UserTopicPracticeValidator:
    """
    The user_topic_practice table contains information about each topic (flashcard)
    that a student is eligible to see for a given topic in a course.
    A particular topic should ony be in the table once per student.  This row also contains
    information about scheduling and correctness to help the practice algorithm select the
    best question to show a student.

    Retrieve a single UserTopicPractice entry for the given user, chapter, and subchapter (i.e., topic).

    :param user: AuthUserValidator, the AuthUserValidator object
    :param last_page_chapter: str, the label of the chapter
    :param last_page_subchapter: str, the label of the subchapter
    :param qname: str, the name of the question
    :return: UserTopicPracticeValidator, the UserTopicPracticeValidator object
    """
    query = select(UserTopicPractice).where(
        (UserTopicPractice.user_id == user.id)
        & (UserTopicPractice.course_name == user.course_name)
        & (UserTopicPractice.chapter_label == last_page_chapter)
        & (UserTopicPractice.sub_chapter_label == last_page_subchapter)
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        utp = res.scalars().first()
        return UserTopicPracticeValidator.from_orm(utp)


async def delete_one_user_topic_practice(dbid: int) -> None:
    """
    Delete a single UserTopicPractice entry for the given id.

    Used by self-paced topic selection.  If a student un-marks a page as completed then if there
    is a card from the page it will be removed from the set of possible flashcards a student
    can see.

    :param qid: int, the id of the UserTopicPractice entry
    :return: None
    """
    query = delete(UserTopicPractice).where(UserTopicPractice.id == dbid)
    async with async_session.begin() as session:
        await session.execute(query)


async def create_user_topic_practice(
    user: AuthUserValidator,
    last_page_chapter: str,
    last_page_subchapter: str,
    qname: str,
    now_local: datetime.datetime,
    now: datetime.datetime,
    tz_offset: float,
):
    """
    Add a new UserTopicPractice entry for the given user, chapter, subchapter, and question.

    :param user: AuthUserValidator, the AuthUserValidator object
    :param last_page_chapter: str, the label of the chapter
    :param last_page_subchapter: str, the label of the subchapter
    :param qname: str, the name of the question to be assigned first when the topic is presented; will be rotated
    :param now_local: datetime.datetime, the current local datetime
    :param now: datetime.datetime, the current utc datetime
    :param tz_offset: float, the timezone offset
    :return: None
    """
    async with async_session.begin() as session:
        new_entry = UserTopicPractice(
            user_id=user.id,
            course_name=user.course_name,
            chapter_label=last_page_chapter,
            sub_chapter_label=last_page_subchapter,
            question_name=qname,
            # Treat it as if the first eligible question is the last one asked.
            i_interval=0,
            e_factor=2.5,
            next_eligible_date=now_local.date(),
            # add as if yesterday, so can practice right away
            last_presented=now - datetime.timedelta(1),
            last_completed=now - datetime.timedelta(1),
            creation_time=now,
            timezoneoffset=tz_offset,
        )
        session.add(new_entry)


async def fetch_qualified_questions(
    base_course: str, chapter_label: str, sub_chapter_label: str
) -> list[QuestionValidator]:
    """
    Retrieve a list of qualified questions for a given chapter and subchapter.

    :param base_course: str, the base course
    :param chapter_label: str, the label of the chapter
    :param sub_chapter_label: str, the label of the subchapter
    :return: list[QuestionValidator], a list of QuestionValidator objects
    """
    query = select(Question).where(
        (Question.base_course == base_course)
        & (
            (Question.topic == f"{chapter_label}/{sub_chapter_label}")
            | (
                (Question.chapter == chapter_label)
                & (Question.topic == None)  # noqa: E711
                & (Question.subchapter == sub_chapter_label)
            )
        )
        & (Question.practice == True)  # noqa: E712
        & (Question.review_flag == False)  # noqa: E712
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        questionlist = [QuestionValidator.from_orm(x) for x in res.scalars().fetchall()]

    return questionlist


async def create_editor_for_basecourse(user_id: int, bc_name: str) -> EditorBasecourse:
    """
    Creates a new editor for a given basecourse.

    :param user_id: The ID of the user creating the editor.
    :type user_id: int
    :param bc_name: The name of the basecourse for which the editor is being created.
    :type bc_name: str
    :return: The newly created editor for the basecourse.
    :rtype: EditorBasecourse
    """
    new_ed = EditorBasecourse(user_id, bc_name)
    async with async_session.begin() as session:
        session.add(new_ed)
    return new_ed


async def is_editor(userid: int) -> bool:
    """
    Checks if a user is an editor.

    :param userid: The ID of the user to check.
    :type userid: int
    :return: True if the user is an editor, False otherwise.
    :rtype: bool
    """
    ed = await fetch_group("editor")
    row = await fetch_membership(ed.id, userid)

    if row:
        return True
    else:
        return False


async def is_author(userid: int) -> bool:
    """
    Checks if a user is an author.

    :param userid: The ID of the user to check.
    :type userid: int
    :return: True if the user is an author, False otherwise.
    :rtype: bool
    """
    ed = await fetch_group("author")
    row = await fetch_membership(ed.id, userid)

    if row:
        return True
    else:
        return False


# Used by the library page
async def get_students_per_basecourse() -> dict:
    """
    Gets the number of students using a book for each course.

    :return: A dictionary containing the course name and the number of students using it.
    :rtype: Dict[str,int]
    """
    query = (
        select(Courses.base_course, func.count(UserCourse.user_id))
        .join(UserCourse, Courses.id == UserCourse.course_id)
        .group_by(Courses.base_course)
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        retval = {}
        for row in res.all():
            retval[row[0]] = row[1]

        return retval


async def get_courses_per_basecourse() -> dict:
    """
    Gets the number of courses using a basecourse.

    :return: A dictionary containing the base course name and the number of courses using it.
    :rtype: Dict[str,int]
    """
    query = select(Courses.base_course, func.count(Courses.id)).group_by(
        Courses.base_course
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        retval = {}
        for row in res.all():
            retval[row[0]] = row[1]

        return retval


async def fetch_questions_for_chapter_subchapter(
    base_course: str,
    skipreading: bool = False,
    from_source_only: bool = True,
    pages_only: bool = False,
) -> List[dict]:
    """
    Fetch all questions for a given base course, where the skipreading and from_source
    flags are set to the given values.

    :param base_course: str, the base course
    :param skipreading: bool, whether to skip questions/sections marked as "skipreading" Usually
       these sections are the Exercises sections at the end of chapters.
    :param from_source_only: bool, whether the question is from the source, if this is True
       then instructor contributed questions will not be included in the result.
    :param pages_only: bool, whether to include only pages for reading assignment creation.
    :return: List[dict], a list of questions in a hierarchical json structure
    """
    if skipreading:
        skipr_clause = SubChapter.skipreading == True  # noqa: E712
    else:
        skipr_clause = True
    if from_source_only:
        froms_clause = Question.from_source == True  # noqa: E712
    else:
        froms_clause = True
    if pages_only:
        page_clause = Question.question_type == "page"
    else:
        page_clause = True
    query = (
        select(Question, Chapter, SubChapter)
        .join(
            Chapter,
            and_(
                Question.chapter == Chapter.chapter_label,
                Question.base_course == Chapter.course_id,
            ),
        )
        .join(
            SubChapter,
            and_(
                SubChapter.chapter_id == Chapter.id,
                Question.subchapter == SubChapter.sub_chapter_label,
            ),
        )
        .where(
            and_(
                Chapter.course_id == base_course,
                skipr_clause,
                froms_clause,
                page_clause,
            )
        )
        .order_by(Chapter.chapter_num, SubChapter.sub_chapter_num, Question.id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        # convert the result to a hierarchical json structure using the chapter and subchapter labels
        questions = {}
        chapters = {}

        for row in res:
            q = QuestionValidator.from_orm(row.Question)
            c = ChapterValidator.from_orm(row.Chapter)
            c.chapter_label = f"{c.chapter_label}"
            sc = SubChapterValidator.from_orm(row.SubChapter)
            sc.sub_chapter_label = f"{sc.sub_chapter_label}"
            if c.chapter_label not in questions:
                questions[c.chapter_label] = {}
            if c.chapter_label not in chapters:
                chapters[c.chapter_label] = {**c.dict(), "sub_chapters": {}}
            if sc.sub_chapter_label not in questions[c.chapter_label]:
                questions[c.chapter_label][sc.sub_chapter_label] = []
            if sc.sub_chapter_label not in chapters[c.chapter_label]["sub_chapters"]:
                chapters[c.chapter_label]["sub_chapters"][sc.sub_chapter_label] = {
                    **sc.dict(),
                }
            del q.timestamp  # Does not convert to json
            del q.question
            questions[c.chapter_label][sc.sub_chapter_label].append(q)

        # Now create the hierarchical json structure where the keys are the chapter and subchapter labels
        # This is the structure that is used by the React TreeTable component
        def find_page_id(chapter, subchapter):
            for q in questions[chapter][subchapter]:
                if q.question_type == "page":
                    return q.id
            return None

        chaps = []
        for chapter in questions:
            subs = []
            for subchapter in questions[chapter]:
                subs.append(
                    {
                        "key": subchapter,
                        "data": {
                            "title": chapters[chapter]["sub_chapters"][subchapter][
                                "sub_chapter_name"
                            ],
                            "num": chapters[chapter]["sub_chapters"][subchapter][
                                "sub_chapter_num"
                            ],
                            "chapter": chapter,
                            "subchapter": subchapter,
                            "id": find_page_id(chapter, subchapter),
                            "numQuestions": len(
                                [
                                    q
                                    for q in questions[chapter][subchapter]
                                    if q.optional != True
                                ]
                            ),
                        },
                        "children": [
                            {"key": q.name, "data": q.dict()}
                            for q in questions[chapter][subchapter]
                        ],
                    }
                )
            chaps.append(
                {
                    "key": chapter,
                    "data": {
                        "title": chapters[chapter]["chapter_name"],
                        "num": chapters[chapter]["chapter_num"],
                    },
                    "children": subs,
                }
            )

        return chaps


async def fetch_answers(question_id: str, event: str, course_name: str, username: str):
    """
    Fetch all answers for a given question.

    :param question_id: int, the id of the question
    :return: List[AnswerValidator], a list of AnswerValidator objects
    """

    rcd = runestone_component_dict[EVENT2TABLE[event]]
    tbl = rcd.model
    query = select(tbl).where(
        and_(
            (tbl.div_id == question_id),
            (tbl.course_name == course_name),
            (tbl.sid == username),
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [a for a in res.scalars()]


async def is_assigned(
    question_id: str,
    course_id: int,
    assignment_id: Optional[int] = None,
    accommodation: Optional[DeadlineExceptionValidator] = None,
) -> schemas.ScoringSpecification:
    """
    Check if a question is part of an assignment.
    If the assignment is not visible, the question is not considered assigned.
    If the assignment is not yet due -- no problem.
    If the assignment is past due but the instructor has not enforced the due date -- no problem.
    If the assignment is past due but the instructor has not enforced the due date, but HAS released the assignment -- then no longer assigned.

    :param question_id: str, the name of the question
    :param course_id: int, the id of the course
    :return: ScoringSpecification, the scoring specification object
    """
    # select * from assignments join assignment_questions on assignment_questions.assignment_id = assignments.id join courses on courses.id = assignments.course where courses.course_name = 'overview'
    clauses = [
        (Question.name == question_id),
        (AssignmentQuestion.question_id == Question.id),
        (AssignmentQuestion.assignment_id == Assignment.id),
        (Assignment.course == course_id),
        (Assignment.is_timed == False),  # noqa: E712
    ]
    if assignment_id is not None:
        clauses.append(Assignment.id == assignment_id)
    query = (
        select(Assignment, AssignmentQuestion, Question)
        .where(and_(*clauses))
        .order_by(Assignment.duedate.desc())
    )
    visible_exception = False
    if accommodation and accommodation.visible:
        visible_exception = True
    async with async_session() as session:
        res = await session.execute(query)
        for row in res:
            scoringSpec = schemas.ScoringSpecification(
                assigned=False,
                max_score=row.AssignmentQuestion.points,
                score=0,
                assignment_id=row.Assignment.id,
                which_to_grade=row.AssignmentQuestion.which_to_grade,
                how_to_score=row.AssignmentQuestion.autograde,
                is_reading=row.AssignmentQuestion.reading_assignment,
                username="",
                comment="",
                question_id=row.Question.id,
            )
            if accommodation and accommodation.duedate:
                row.Assignment.duedate += datetime.timedelta(days=accommodation.duedate)
            if datetime.datetime.now(datetime.UTC) <= row.Assignment.duedate.replace(
                tzinfo=pytz.utc
            ):
                if row.Assignment.visible:  # todo update this when we have a visible by
                    scoringSpec.assigned = True
                    return scoringSpec
            else:
                if not row.Assignment.enforce_due and not row.Assignment.released:
                    if row.Assignment.visible or visible_exception:
                        scoringSpec.assigned = True
                        return scoringSpec
        return schemas.ScoringSpecification()


async def fetch_reading_assignment_spec(
    chapter: str,
    subchapter: str,
    course_id: int,
) -> Optional[int]:
    """
    Check if a reading assignment is assigned for a given chapter and subchapter.

    :param chapter: str, the label of the chapter
    :param subchapter: str, the label of the subchapter
    :param course_id: int, the id of the course
    :return: The number of required activities or None if not found
    """
    query = (
        select(
            AssignmentQuestion.activities_required,
            AssignmentQuestion.question_id,
            AssignmentQuestion.points,
            AssignmentQuestion.assignment_id,
            Question.name,
        )
        .select_from(Assignment)
        .join(AssignmentQuestion, AssignmentQuestion.assignment_id == Assignment.id)
        .join(Question, Question.id == AssignmentQuestion.question_id)
        .where(
            and_(
                Assignment.course == course_id,
                AssignmentQuestion.reading_assignment == True,  # noqa: E712
                Question.chapter == chapter,
                Question.subchapter == subchapter,
                Assignment.visible == True,  # noqa: E712
                or_(
                    Assignment.duedate > canonical_utcnow(),
                    Assignment.enforce_due == False,  # noqa: E712
                ),
            )
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        return res.first()


async def fetch_assignment_scores(
    assignment_id: int, course_name: str, username: str
) -> List[QuestionGradeValidator]:
    """
    Fetch all scores for a given assignment.

    :param assignment_id: int, the id of the assignment
    :param course_id: int, the id of the course
    :param username: str, the username of the student
    :return: List[ScoringSpecification], a list of ScoringSpecification objects
    """
    query = select(QuestionGrade).where(
        and_(
            (QuestionGrade.sid == username),
            (QuestionGrade.div_id == Question.name),
            (Question.id == AssignmentQuestion.question_id),
            (AssignmentQuestion.assignment_id == assignment_id),
            (QuestionGrade.course_name == course_name),
        )
    )

    async with async_session() as session:
        res = await session.execute(query)
        rslogger.debug(f"{res=}")
        return [QuestionGradeValidator.from_orm(q) for q in res.scalars()]


async def did_send_messages(sid: str, div_id: str, course_name: str) -> bool:
    """
    Fetch all messages sent to a given student.

    :param sid: str, the student id
    :return: List[SentMessageValidator], a list of SentMessageValidator objects
    """
    query = select(Useinfo).where(
        and_(
            (Useinfo.sid == sid),
            (Useinfo.div_id == div_id),
            (Useinfo.course_id == course_name),
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        if len(res.all()) > 0:
            return True
        else:
            return False


async def fetch_lti_version(course_id: int) -> str:
    """
    Check if a course uses LTI 1.1, 1.3 or none

    :param course_id: int, the id of the course
    :return: str for LTI version (1.1 or 1.3) or None
    """
    query = select(CourseLtiMap).where(CourseLtiMap.course_id == course_id)
    query2 = select(Lti1p3Course).where(Lti1p3Course.rs_course_id == course_id)
    async with async_session() as session:
        res = await session.execute(query)
        if len(res.all()) > 0:
            return "1.1"

        res2 = await session.execute(query2)
        if len(res2.all()) > 0:
            return "1.3"

        return None


async def create_lti_course(course_id: int, lti_id: str) -> CourseLtiMap:
    """
    Create a new course in the LTI map.

    :param course_id: int, the id of the course
    :param lti_id: str, the LTI id of the course
    :return: CourseLtiMap, the CourseLtiMap object
    """
    new_entry = CourseLtiMap(course_id=course_id, lti_id=lti_id)
    async with async_session.begin() as session:
        session.add(new_entry)

    return new_entry


async def delete_lti_course(course_id: int) -> bool:
    """
    Delete a course from the LTI map.

    :param course_id: int, the id of the course
    """
    query = select(CourseLtiMap).where(CourseLtiMap.course_id == course_id)
    async with async_session() as session:
        res = await session.execute(query)
    if res:
        lti_key = res.scalars().first().lti_id
    else:
        return False

    d_query1 = delete(CourseLtiMap).where(CourseLtiMap.course_id == course_id)
    d_query2 = delete(LtiKey).where(LtiKey.id == lti_key)
    async with async_session.begin() as session:
        await session.execute(d_query1)
        await session.execute(d_query2)

    return True


# -----------------------------------------------------------------------
# LTI 1.3
async def upsert_lti1p3_config(config: Lti1p3Conf) -> Lti1p3Conf:
    """
    Insert or update an LTI1.3 platform config.
    issuer and client_id must be provided. If they match an existing record,
    all other fields are optional and only need to be provided if they are to be updated.
    """
    async with async_session() as session:
        query = select(Lti1p3Conf).where(
            (Lti1p3Conf.issuer == config.issuer)
            & (Lti1p3Conf.client_id == config.client_id)
        )
        res = await session.execute(query)
        existing_conf = res.scalars().one_or_none()
        await session.commit()
        if existing_conf:
            existing_conf.update_from_dict(config.dict())
            # Validate now that we have built full object
            Lti1p3ConfValidator.from_orm(existing_conf)
            ret = existing_conf
        else:
            Lti1p3ConfValidator.from_orm(config)  # validate data
            session.add(config)
            ret = config
        await session.commit()
        return ret


async def fetch_lti1p3_config(id: int) -> Lti1p3Conf:
    """
    Retrieve an LTI1.3 platform configuration
    """
    query = select(Lti1p3Conf).where((Lti1p3Conf.id == id))
    async with async_session() as session:
        res = await session.execute(query)
        conf = res.scalars().one_or_none()
        return conf


async def fetch_lti1p3_config_by_lti_data(issuer: str, client_id: str) -> Lti1p3Conf:
    """
    Retrieve an LTI1.3 platform config by issuer and client_id.
    """
    query = select(Lti1p3Conf).where(
        (Lti1p3Conf.issuer == issuer) & (Lti1p3Conf.client_id == client_id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        conf = res.scalars().one_or_none()
        return conf


async def upsert_lti1p3_course(course: Lti1p3Course) -> Lti1p3Course:
    """
    Insert or update an LTI1.3 course.

    rs_course_id must be provided and will be used to identify the record to update.
    all other fields are optional and only need to be provided if they are to be updated.
    """
    async with async_session() as session:
        query = select(Lti1p3Course).where(
            (Lti1p3Course.rs_course_id == course.rs_course_id)
        )
        res = await session.execute(query)
        existing_course = res.scalars().one_or_none()
        await session.commit()
        if existing_course:
            existing_course.update_from_dict(course.dict())
            # Validate now that we have built full object
            Lti1p3CourseValidator.from_orm(existing_course)
            ret = existing_course
        else:
            Lti1p3CourseValidator.from_orm(course)
            session.add(course)
            ret = course
        await session.commit()
        return ret


async def delete_lti1p3_course(rs_course_id: int) -> int:
    """
    Delete an LTI1.3 course mapping by the rs_course_id it is associated with
    """
    query = delete(Lti1p3Course).where(Lti1p3Course.rs_course_id == rs_course_id)
    async with async_session() as session:
        res = await session.execute(query)
        await session.commit()
        return res.rowcount


async def fetch_lti1p3_course(
    id: int, with_config: bool = True, with_rs_course: bool = False
) -> Lti1p3Course:
    """
    Retrieve an LTI1.3 course by its id
    Also optionally fetches the associated Lti1p3Conf and/or RS Course
    """
    query = select(Lti1p3Course).where(Lti1p3Course.id == id)
    if with_config:
        query = query.options(joinedload(Lti1p3Course.lti_config))
    if with_rs_course:
        query = query.options(joinedload(Lti1p3Course.rs_course))
    async with async_session() as session:
        res = await session.execute(query)
        course = res.scalars().one_or_none()
        return course


async def fetch_lti1p3_course_by_rs_course(
    rs_course: CoursesValidator, with_config: bool = True
) -> Lti1p3Course:
    """
    Retrieve an LTI1.3 platform config by its id
    Also optionally fetches the associated Lti1p3Conf and/or Course
    """
    query = select(Lti1p3Course).where(Lti1p3Course.rs_course_id == rs_course.id)
    if with_config:
        query = query.options(joinedload(Lti1p3Course.lti_config))
    async with async_session() as session:
        res = await session.execute(query)
        course = res.scalars().one_or_none()
        return course


async def fetch_lti1p3_course_by_id(
    id: int, with_config: bool = True, with_rs_course: bool = False
) -> Lti1p3Course:
    """
    Retrieve an LTI1.3 platform config by its id
    Also optionally fetches the associated Lti1p3Conf and/or Course
    """
    query = select(Lti1p3Course).where(Lti1p3Course.id == id)
    if with_config:
        query = query.options(joinedload(Lti1p3Course.lti_config))
    if with_rs_course:
        query = query.options(joinedload(Lti1p3Course.rs_course))
    async with async_session() as session:
        res = await session.execute(query)
        dep = res.scalars().one_or_none()
        return dep


async def fetch_lti1p3_course_by_lti_id(
    lti_id: str, with_config: bool = True, with_rs_course: bool = False
) -> Lti1p3Course:
    """
    Retrieve an LTI1.3 platform config by its lti identifier
    Also optionally fetches the associated Lti1p3Conf and/or Course
    """
    query = select(Lti1p3Course).where(Lti1p3Course.lti1p3_course_id == lti_id)
    if with_config:
        query = query.options(joinedload(Lti1p3Course.lti_config))
    if with_rs_course:
        query = query.options(joinedload(Lti1p3Course.rs_course))
    async with async_session() as session:
        res = await session.execute(query)
        dep = res.scalars().one_or_none()
        return dep


async def fetch_lti1p3_course_by_lti_data(
    issuer: str, client_id: str, deploy_id: str, with_config: bool = True
) -> Lti1p3Course:
    """
    Retrieve an LTI1.3 platform config by issuer and client_id.
    Also fetches the associated Lti1p3Conf
    """
    query = (
        select(Lti1p3Course)
        .join(Lti1p3Conf)
        .where(
            (Lti1p3Conf.issuer == issuer)
            & (Lti1p3Conf.client_id == client_id)
            & (Lti1p3Course.deployment_id == deploy_id)
        )
    )
    if with_config:
        query = query.options(joinedload(Lti1p3Course.lti_config))
    async with async_session() as session:
        res = await session.execute(query)
        dep = res.scalars().one_or_none()
        return dep


async def upsert_lti1p3_user(user: Lti1p3User) -> Lti1p3User:
    """
    Insert or update an LTI1.3 user mapping for a particular course
    """
    async with async_session() as session:
        query = select(Lti1p3User).where(
            (Lti1p3User.lti1p3_course_id == user.lti1p3_course_id)
            & (Lti1p3User.rs_user_id == user.rs_user_id)
        )
        res = await session.execute(query)
        existing_user = res.scalars().one_or_none()
        await session.commit()
        if existing_user:
            # never should never need to update lti_user_id
            return existing_user
        else:
            new_user = Lti1p3User(
                lti1p3_course_id=user.lti1p3_course_id,
                rs_user_id=user.rs_user_id,
                lti_user_id=user.lti_user_id,
            )
            Lti1p3UserValidator.from_orm(new_user)
            session.add(new_user)
            await session.commit()
            return new_user


async def fetch_lti1p3_user(rs_user_id: int, lti1p3_course_id: int) -> Lti1p3User:
    """
    Retrieve a user's LTI1.3 mapping for a particular course
    """
    query = select(Lti1p3User).where(
        (Lti1p3User.rs_user_id == rs_user_id)
        & (Lti1p3User.lti1p3_course_id == lti1p3_course_id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        user = res.scalars().one_or_none()
        return user


async def fetch_lti1p3_users_for_course(
    lti1p3_course_id: int, with_rsuser: bool = True
) -> List[Lti1p3User]:
    """
    Retrieve all LTI1.3 user mapping for a particular course
    """
    query = select(Lti1p3User).where((Lti1p3User.lti1p3_course_id == lti1p3_course_id))
    if with_rsuser:
        query = query.options(joinedload(Lti1p3User.rs_user))
    async with async_session() as session:
        res = await session.execute(query)
        users = res.scalars().all()
        return users


async def upsert_lti1p3_assignment(assignment: Lti1p3Assignment) -> Lti1p3Assignment:
    """
    Insert or update an LTI1.3 assignment mapping.
    """
    async with async_session() as session:
        query = select(Lti1p3Assignment).where(
            (Lti1p3Assignment.lti1p3_course_id == assignment.lti1p3_course_id)
            & (Lti1p3Assignment.rs_assignment_id == assignment.rs_assignment_id)
        )
        res = await session.execute(query)
        existing_assignment = res.scalars().one_or_none()
        await session.commit()
        if existing_assignment:
            existing_assignment.update_from_dict(assignment.dict())
            # Validate now that we have built full object
            Lti1p3AssignmentValidator.from_orm(existing_assignment)
            ret = existing_assignment
        else:
            new_assignment = Lti1p3Assignment(
                lti1p3_course_id=assignment.lti1p3_course_id,
                rs_assignment_id=assignment.rs_assignment_id,
                lti_lineitem_id=assignment.lti_lineitem_id,
            )
            Lti1p3AssignmentValidator.from_orm(new_assignment)
            session.add(new_assignment)
            ret = new_assignment
        await session.commit()
        return ret


async def fetch_lti1p3_assignments_by_rs_assignment_id(
    rs_assignment_id: int,
) -> Lti1p3Assignment:
    """
    Retrieve an LTI1.3 assignment mapping. There may be more than record as one RS course
    might be mapped to multiple different LTI assignments.
    """
    query = select(Lti1p3Assignment).where(
        (Lti1p3Assignment.rs_assignment_id == rs_assignment_id)
    )
    async with async_session() as session:
        res = await session.execute(query)
        assignment = res.scalars().all()
        return assignment


async def fetch_lti1p3_assignments_by_rs_course_id(
    rs_course_id: int,
) -> List[Lti1p3Assignment]:
    """
    Retrieve all LTI1.3 assignment mappings for a course
    """
    query = (
        select(Lti1p3Assignment)
        .join(Assignment)
        .where((Assignment.course == rs_course_id))
    )
    async with async_session() as session:
        res = await session.execute(query)
        assignments = res.scalars().all()
        return assignments


async def fetch_lti1p3_grading_data_for_assignment(
    rs_assignment_id: int,
) -> Lti1p3Assignment:
    """
    Fetch data needed to submit grades for a particular assignment
    """
    async with async_session() as session:
        query = (
            select(Lti1p3Assignment)
            .join(Lti1p3Course, Lti1p3Course.id == Lti1p3Assignment.lti1p3_course_id)
            .where(Lti1p3Assignment.rs_assignment_id == rs_assignment_id)
            .options(
                joinedload(Lti1p3Assignment.rs_assignment),
                joinedload(Lti1p3Assignment.lti1p3_course),
                joinedload(Lti1p3Assignment.lti1p3_course).joinedload(
                    Lti1p3Course.lti_config
                ),
                joinedload(Lti1p3Assignment.lti1p3_course).joinedload(
                    Lti1p3Course.rs_course
                ),
            )
        )
        res = await session.execute(query)
        assign = res.scalars().one_or_none()
        return assign


# /LTI 1.3
# -----------------------------------------------------------------------


async def create_invoice_request(
    user_id: str, course_name: str, amount: float, email: str
) -> InvoiceRequest:
    """
    Create a new invoice request.

    :param user_id: str, the id of the user
    :param course_name: str, the name of the course
    :param amount: float, the amount of the invoice
    :param email: str, the email address of the user
    :return: InvoiceRequest, the InvoiceRequest object
    """
    new_entry = InvoiceRequest(
        sid=user_id,
        course_name=course_name,
        amount=amount,
        email=email,
        timestamp=canonical_utcnow(),
        processed=False,
    )
    async with async_session.begin() as session:
        session.add(new_entry)

    return new_entry


async def fetch_last_useinfo_peergroup(course_name: str) -> List[Useinfo]:
    """
    Fetch the last peergroup entry for each student in the given course.

    :param course_name: str, the name of the course
    :return: List[Useinfo], a list of Useinfo objects
    """
    async with async_session.begin() as session:
        # Aliases for the Useinfo table
        u1 = aliased(Useinfo)
        u2 = aliased(Useinfo)

        # Subquery to get the last entry for each student
        subquery = (
            select(u2.sid, func.max(u2.timestamp).label("last_entry"))
            .filter(and_(u2.course_id == course_name, u2.event == "peergroup"))
            .group_by(u2.sid)
            .subquery()
        )

        # Main query to join the subquery and get the last entry details
        query = (
            select(u1)
            .join(
                subquery,
                (u1.sid == subquery.c.sid) & (u1.timestamp == subquery.c.last_entry),
            )
            .filter(and_(u1.course_id == course_name, u1.event == "peergroup"))
        )

        # Execute the query
        results = await session.execute(query)
        return results.scalars().all()


# We need a synchronous version of this function for use in manifest_data_to_db
# if/when process_manifest moves to being async we could remove this
def update_source_code_sync(acid: str, filename: str, course_id: str, main_code: str):
    """
    Update the source code for a given acid or filename
    """
    query = select(SourceCode).where(
        and_(
            SourceCode.acid == acid,
            SourceCode.course_id == course_id,
        )
    )
    with sync_session() as session:
        res = session.execute(query)
        source_code_obj = res.scalars().first()
        if source_code_obj:
            source_code_obj.main_code = main_code
            source_code_obj.filename = filename
            session.add(source_code_obj)
        else:
            new_entry = SourceCode(
                acid=acid,
                filename=filename,
                course_id=course_id,
                main_code=main_code,
            )
            session.add(new_entry)
        session.commit()


async def update_source_code(acid: str, filename: str, course_id: str, main_code: str):
    """
    Update the source code for a given acid or filename
    """
    query = select(SourceCode).where(
        and_(
            SourceCode.acid == acid,
            SourceCode.course_id == course_id,
        )
    )
    async with async_session() as session:
        res = await session.execute(query)
        source_code_obj = res.scalars().first()
        if source_code_obj:
            source_code_obj.main_code = main_code
            source_code_obj.filename = filename
            session.add(source_code_obj)
        else:
            new_entry = SourceCode(
                acid=acid,
                filename=filename,
                course_id=course_id,
                main_code=main_code,
            )
            session.add(new_entry)
        await session.commit()


async def fetch_source_code(
    base_course: str, course_name: str, acid: str = None, filename: str = None
) -> SourceCodeValidator:
    """
    Fetch the source code for a given acid or filename

    Note that filenames are not guaranteed to be unique within a course, so
    acid is the preferred lookup method.

    :param acid: str, the acid of the source code
    :return: SourceCodeValidator, the SourceCodeValidator object
    """
    rslogger.debug(f"fetch_source_code: -{acid}-{filename}-{course_name}-{base_course}")
    if acid is None and filename is None:
        return None
    elif acid is None:
        # match against filename or acid for backwards compatibility
        query = select(SourceCode).where(
            and_(
                or_(
                    SourceCode.filename == filename,
                    SourceCode.acid == filename,
                ),
                or_(
                    SourceCode.course_id == base_course,
                    SourceCode.course_id == course_name,
                ),
            )
        )
    else:
        query = select(SourceCode).where(
            and_(
                SourceCode.acid == acid,
                or_(
                    SourceCode.course_id == base_course,
                    SourceCode.course_id == course_name,
                ),
            )
        )
    async with async_session() as session:
        res = await session.execute(query)
        return SourceCodeValidator.from_orm(res.scalars().first())


async def fetch_deadline_exception(
    course_id: int, username: str, assignment_id: int = None, fetch_all: bool = False
) -> DeadlineExceptionValidator:
    """
    Fetch the deadline exception for a given username and assignment_id.

    :param username: str, the username of the student
    :param assignment_id: int, the id of the assignment
    :return: DeadlineExceptionValidator, the DeadlineExceptionValidator object
    """
    query = (
        select(DeadlineException)
        .where(
            and_(
                DeadlineException.course_id == course_id,
                DeadlineException.sid == username,
            )
        )
        .order_by(DeadlineException.id.desc())
    )
    time_limit = None
    deadline = None
    async with async_session() as session:
        res = await session.execute(query)
        if fetch_all:
            return [
                DeadlineExceptionValidator.from_orm(row)
                for row in res.scalars().fetchall()
            ]
        for row in res.scalars().fetchall():
            rslogger.debug(f"{row=}, {assignment_id=}")
            if assignment_id is not None:
                if row.assignment_id == assignment_id:
                    return DeadlineExceptionValidator.from_orm(row)
            else:
                if row.time_limit is not None and row.assignment_id is None:
                    time_limit = row.time_limit
                if row.duedate is not None and row.assignment_id is None:
                    deadline = row.duedate
        return DeadlineExceptionValidator(
            course_id=course_id, sid=username, time_limit=time_limit, duedate=deadline
        )


async def create_deadline_exception(
    course_id: int,
    username: str,
    time_limit: float,
    deadline: int,
    visible: bool,
    assignment_id: int = None,
) -> DeadlineExceptionValidator:
    """
    Create a new deadline exception for a given username and assignment_id.

    :param username: str, the username of the student
    :param assignment_id: int, the id of the assignment
    :return: DeadlineExceptionValidator, the DeadlineExceptionValidator object
    """
    new_entry = DeadlineException(
        course_id=course_id,
        sid=username,
        time_limit=time_limit,
        duedate=deadline,
        visible=visible,
        assignment_id=assignment_id,
    )
    async with async_session.begin() as session:
        session.add(new_entry)

    return DeadlineExceptionValidator.from_orm(new_entry)


async def get_repo_path(book: str) -> Optional[str]:
    """
    Get the repo_path for a book from the library table

    :param book: book name (basecourse)
    :return: repo_path or None if not found
    """
    async with async_session() as session:
        query = select(Library.repo_path).where(Library.basecourse == book)
        result = await session.execute(query)
        repo_path = result.scalar()
        return repo_path


# new function to create encrypted API tokens
async def create_api_token(
    course_id: int,
    provider: str,
    token: str,
) -> APITokenValidator:
    """
    Create a new API token for a course with encrypted storage.

    :param course_id: int, the id of the course
    :param provider: str, the provider name
    :param token: str, plaintext token to encrypt and store
    :return: APITokenValidator, the newly created token record
    """
    new_token = APIToken(course_id=course_id, provider=provider, token=token)
    async with async_session.begin() as session:
        session.add(new_token)
    return APITokenValidator.from_orm(new_token)


async def fetch_api_token(
    course_id: int,
    provider: str,
) -> Optional[APITokenValidator]:
    """
    Fetch the least recently used API token for a given course and provider.
    Updates the last_used field to the current datetime when returning a token.

    :param course_id: int, the id of the course
    :param provider: str, the provider name
    :return: Optional[APITokenValidator], the least recently used token or None if not found
    """
    query = (
        select(APIToken)
        .where((APIToken.course_id == course_id) & (APIToken.provider == provider))
        .order_by(
            APIToken.last_used.asc().nulls_first()
        )  # Least recently used first, NULL values first
        .limit(1)
    )

    async with async_session.begin() as session:
        res = await session.execute(query)
        token = res.scalars().first()
        if token:
            # Update the last_used field to current datetime
            token.last_used = canonical_utcnow()
            session.add(token)
            return APITokenValidator.from_orm(token)
        return None


# DomainApprovals
# ------------------
async def check_domain_approval(
    course_id: int, approval_type: attributes.InstrumentedAttribute
) -> bool:
    """
    Check if a domain approval exists for a given course and approval type.
    :param course_id: int, the id of the course
    :param approval_type: sqlalchemy.orm.attributes.InstrumentedAttribute, the type of approval (e.g., 'DomainApprovals.lti1p3')
    """
    query = (
        select(Courses.domain_name)
        .join(DomainApprovals, Courses.domain_name == DomainApprovals.domain_name)
        .where((approval_type == True) & (Courses.id == course_id))
    )
    async with async_session() as session:
        res = await session.execute(query)
        domain = res.scalars().first()
        return domain is not None


async def delete_course_instructor(course_id: int, instructor_id: int) -> None:
    """
    Remove an instructor from a course by deleting the CourseInstructor relationship.

    :param course_id: int, the id of the course
    :param instructor_id: int, the id of the instructor to remove
    :return: None
    """
    stmt = delete(CourseInstructor).where(
        (CourseInstructor.course == course_id)
        & (CourseInstructor.instructor == instructor_id)
    )

    async with async_session() as session:
        await session.execute(stmt)
        await session.commit()

async def create_course_instructor(course_id: int, instructor_id: int) -> None:
    """
    Add an instructor to a course by creating a new CourseInstructor relationship.

    :param course_id: int, the id of the course
    :param instructor_id: int, the id of the instructor to add
    :return: None
    """
    new_entry = CourseInstructor(course=course_id, instructor=instructor_id)
    async with async_session.begin() as session:
        session.add(new_entry)


async def update_course_settings(course_id: int, setting: str, value: str) -> None:
    """
    Update a course setting/attribute. Handles both special course table fields
    and course attributes.

    :param course_id: int, the id of the course
    :param setting: str, the setting name to update
    :param value: str, the value to set
    :return: None
    :raises ValueError: If date format is invalid for new_date setting
    """
    async with async_session() as session:
        # Handle special course table fields
        if setting in ["new_date", "allow_pairs", "downloads_enabled"]:
            if setting == "new_date":
                # Update term_start_date in courses table
                import datetime

                try:
                    new_date = datetime.datetime.strptime(value, "%Y-%m-%d").date()
                    stmt = (
                        update(Courses)
                        .where(Courses.id == course_id)
                        .values(term_start_date=new_date)
                    )
                    await session.execute(stmt)
                except ValueError:
                    raise ValueError("Invalid date format")

            elif setting == "allow_pairs":
                bool_val = value.lower() == "true"
                stmt = (
                    update(Courses)
                    .where(Courses.id == course_id)
                    .values(allow_pairs=bool_val)
                )
                await session.execute(stmt)

            elif setting == "downloads_enabled":
                bool_val = value.lower() == "true"
                stmt = (
                    update(Courses)
                    .where(Courses.id == course_id)
                    .values(downloads_enabled=bool_val)
                )
                await session.execute(stmt)
        else:
            # Handle course attributes
            # Check if attribute exists
            stmt = select(CourseAttribute).where(
                (CourseAttribute.course_id == course_id)
                & (CourseAttribute.attr == setting)
            )
            result = await session.execute(stmt)
            existing_attr = result.scalar_one_or_none()

            if existing_attr:
                # Update existing attribute
                stmt = (
                    update(CourseAttribute)
                    .where(
                        (CourseAttribute.course_id == course_id)
                        & (CourseAttribute.attr == setting)
                    )
                    .values(value=str(value))
                )
                await session.execute(stmt)
            else:
                # Create new attribute
                new_attr = CourseAttribute(
                    course_id=course_id,
                    attr=setting,
                    value=str(value),
                )
                session.add(new_attr)

        await session.commit()


# -----------------------------------------------------------------------
# Assessment Reset Functions
# -----------------------------------------------------------------------


async def fetch_timed_assessments(course_id: int) -> List[Tuple[str, str]]:
    """
    Retrieve all timed assessments for a given course.

    :param course_id: int, the course id
    :return: List[Tuple[str, str]], list of (name, description) tuples for timed assessments
    """
    query = (
        select(Assignment.name, Assignment.description)
        .where((Assignment.course == course_id) & (Assignment.is_timed == "T"))
        .order_by(Assignment.name)
    )

    async with async_session() as session:
        res = await session.execute(query)
        return [(row.name, row.description or "") for row in res.all()]


async def reset_student_assessment(
    username: str, assessment_name: str, course_name: str
) -> bool:
    """
    Reset a student's timed assessment by:
    1. Updating useinfo records to set act="start_reset"
    2. Deleting timed_exam records
    3. Deleting selected_questions records for exam questions

    :param username: str, the student's username
    :param assessment_name: str, the name of the assessment to reset
    :param course_name: str, the name of the course
    :return: bool, True if reset was successful
    """
    try:
        async with async_session.begin() as session:
            # Update useinfo records for this exam to mark as reset
            useinfo_update = (
                update(Useinfo)
                .where(
                    (Useinfo.sid == username)
                    & (Useinfo.div_id == assessment_name)
                    & (Useinfo.course_id == course_name)
                    & (Useinfo.event == "timedExam")
                )
                .values(act="start_reset")
            )
            await session.execute(useinfo_update)

            # Delete timed_exam records
            timed_exam_delete = delete(TimedExam).where(
                (TimedExam.sid == username)
                & (TimedExam.div_id == assessment_name)
                & (TimedExam.course_name == course_name)
            )
            await session.execute(timed_exam_delete)

            # Get all question names for this assessment
            assessment_questions_query = (
                select(Question.name)
                .join(AssignmentQuestion, Question.id == AssignmentQuestion.question_id)
                .join(Assignment, AssignmentQuestion.assignment_id == Assignment.id)
                .where(Assignment.name == assessment_name)
            )
            question_result = await session.execute(assessment_questions_query)
            question_names = [row.name for row in question_result.scalars().all()]

            # Delete selected_questions records for all questions in this exam
            if question_names:
                selected_questions_delete = delete(SelectedQuestion).where(
                    (SelectedQuestion.sid == username)
                    & (SelectedQuestion.selector_id.in_(question_names))
                )
                await session.execute(selected_questions_delete)

            return True

    except Exception as e:
        rslogger.error(f"Error deleting course {course_name}: {e}")
        return False


# -----------------------------------------------------------------------
# Course Deletion Functions
# -----------------------------------------------------------------------


async def delete_course_completely(course_name: str) -> bool:
    """
    Completely delete a course and all associated data.

    WARNING: This is a destructive operation that cannot be undone.

    This function will delete:
    - All student enrollments in the course
    - All assignments and grades
    - All course sections
    - Student progress data (useinfo, timed_exam, etc.)
    - Course customizations and settings
    - LTI integrations

    :param course_name: str, the name of the course to delete
    :return: bool, True if deletion was successful
    """
    try:
        async with async_session.begin() as session:
            # First, get the course information
            course_query = select(Courses).where(Courses.course_name == course_name)
            course_result = await session.execute(course_query)
            course = course_result.scalar_one_or_none()

            if not course:
                rslogger.warning(f"Course {course_name} not found for deletion")
                return False

            course_id = course.id
            rslogger.info(
                f"Starting deletion of course: {course_name} (ID: {course_id})"
            )

            # Delete in order to respect foreign key constraints

            # 1. Delete student progress and activity data
            rslogger.info("Deleting student activity data...")

            # Delete useinfo records
            await session.execute(
                delete(Useinfo).where(Useinfo.course_id == course_name)
            )

            # Delete timed exam records
            await session.execute(
                delete(TimedExam).where(TimedExam.course_name == course_name)
            )

            # Delete multiple choice answers
            await session.execute(
                delete(MchoiceAnswers).where(MchoiceAnswers.course_name == course_name)
            )

            # Delete fill-in-the-blank answers
            await session.execute(
                delete(FitbAnswers).where(FitbAnswers.course_name == course_name)
            )

            # Delete drag and drop answers
            await session.execute(
                delete(DragndropAnswers).where(
                    DragndropAnswers.course_name == course_name
                )
            )

            # Delete clickable area answers
            await session.execute(
                delete(ClickableareaAnswers).where(
                    ClickableareaAnswers.course_name == course_name
                )
            )

            # Delete parsons answers
            await session.execute(
                delete(ParsonsAnswers).where(ParsonsAnswers.course_name == course_name)
            )

            # Delete short answer responses
            await session.execute(
                delete(ShortanswerAnswers).where(
                    ShortanswerAnswers.course_name == course_name
                )
            )

            # Delete coding answers
            await session.execute(delete(Code).where(Code.course_id == course_name))

            # Note: PollAnswer model not found in imports, skipping poll responses deletion
            # await session.execute(delete(PollAnswer).where(PollAnswer.course_name == course_name))

            # Delete selected questions for this course's students
            # This is more complex as we need to find students in the course first
            student_query = select(AuthUser.username).where(
                AuthUser.course_id == course_id
            )
            student_result = await session.execute(student_query)
            student_usernames = [row.username for row in student_result.scalars().all()]

            if student_usernames:
                await session.execute(
                    delete(SelectedQuestion).where(
                        SelectedQuestion.sid.in_(student_usernames)
                    )
                )

            # 2. Delete grading and assignment data
            rslogger.info("Deleting grades and assignments...")

            # Delete question grades for students in this course
            await session.execute(
                delete(QuestionGrade).where(QuestionGrade.course_name == course_name)
            )

            # Delete assignment grades for students in this course
            assignment_query = select(Assignment.id).where(
                Assignment.course == course_id
            )
            assignment_result = await session.execute(assignment_query)
            assignment_ids = [row.id for row in assignment_result.scalars().all()]

            if assignment_ids:
                await session.execute(
                    delete(Grade).where(Grade.assignment.in_(assignment_ids))
                )

            # Delete assignment questions
            if assignment_ids:
                await session.execute(
                    delete(AssignmentQuestion).where(
                        AssignmentQuestion.assignment_id.in_(assignment_ids)
                    )
                )

            # Delete assignments
            await session.execute(
                delete(Assignment).where(Assignment.course == course_id)
            )

            # 3. Delete course instructor relationships
            rslogger.info("Deleting instructor relationships...")
            await session.execute(
                delete(CourseInstructor).where(CourseInstructor.course == course_id)
            )

            # 4. Delete course attributes/settings
            rslogger.info("Deleting course settings...")
            await session.execute(
                delete(CourseAttribute).where(CourseAttribute.course_id == course_id)
            )

            # 5. Delete practice/flashcard data
            rslogger.info("Deleting practice data...")
            await session.execute(
                delete(UserTopicPractice).where(
                    UserTopicPractice.course_name == course_name
                )
            )
            await session.execute(
                delete(UserTopicPracticeCompletion).where(
                    UserTopicPracticeCompletion.course_name == course_name
                )
            )
            await session.execute(
                delete(UserTopicPracticeFeedback).where(
                    UserTopicPracticeFeedback.course_name == course_name
                )
            )

            # 6. Delete payment/invoice data if exists
            rslogger.info("Deleting payment data...")
            # Note: We may want to preserve some payment data for accounting purposes
            # For now, we'll delete invoice requests but preserve actual payments
            if student_usernames:
                student_ids_query = select(AuthUser.id).where(
                    AuthUser.username.in_(student_usernames)
                )
                student_ids_result = await session.execute(student_ids_query)
                student_ids = [row.id for row in student_ids_result.scalars().all()]

                if student_ids:
                    await session.execute(
                        delete(InvoiceRequest).where(
                            InvoiceRequest.user_id.in_(student_ids)
                        )
                    )

            # 7. Update student enrollments - move them to a default course or mark them inactive
            rslogger.info("Updating student enrollments...")
            # Instead of deleting users, we'll move them to a default "orphaned" course
            # or set their course_id to None/default

            # Option 1: Set course_id to None (they become unenrolled)
            await session.execute(
                update(AuthUser)
                .where(AuthUser.course_id == course_id)
                .values(course_id=None, active="F")
            )

            # Option 2: Alternative - move to a default "orphaned students" course
            # This would require creating such a course first
            # default_course_query = select(Courses).where(Courses.course_name == "orphaned_students")
            # default_course = await session.execute(default_course_query)
            # if default_course.scalar_one_or_none():
            #     await session.execute(
            #         update(AuthUser)
            #         .where(AuthUser.course_id == course_id)
            #         .values(course_id=default_course.scalar_one().id)
            #     )

            # 8. Finally, delete the course itself
            rslogger.info("Deleting course record...")
            await session.execute(delete(Courses).where(Courses.id == course_id))

            rslogger.info(f"Successfully deleted course: {course_name}")
            return True

    except Exception as e:
        rslogger.error(f"Error deleting course {course_name}: {e}")
        return False


async def fetch_available_students_for_instructor_add(course_id: int) -> List[Dict[str, Any]]:
    """
    Fetch students in the course who are not already instructors.
    """
    async with async_session() as session:
        students_stmt = (
            select(AuthUser)
            .join(UserCourse, AuthUser.id == UserCourse.user_id)
            .where(UserCourse.course_id == course_id)
        )
        students = (await session.execute(students_stmt)).scalars().all()
        instructors_stmt = select(CourseInstructor.instructor).where(
            CourseInstructor.course == course_id
        )
        instructor_ids = set((await session.execute(instructors_stmt)).scalars().all())
        available_students = [AuthUserValidator.from_orm(s).model_dump() for s in students if s.id not in instructor_ids]
        return available_students


async def fetch_current_instructors_for_course(course_id: int) -> List[Dict[str, Any]]:
    """
    Fetch all instructors for a given course.
    """
    async with async_session() as session:
        stmt = (
            select(AuthUser)
            .join(CourseInstructor, AuthUser.id == CourseInstructor.instructor)
            .where(CourseInstructor.course == course_id)
        )
        instructors = (await session.execute(stmt)).scalars().all()
        return [AuthUserValidator.from_orm(i).model_dump() for i in instructors]
