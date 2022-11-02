"""Library for data model for local calendar objects.

This librayr contains [pydantic](https://pydantic-docs.helpmanual.io/) models
for the Google Calendar API data model. These objects support all methods for
parsing and serialization supported by pydnatic.
"""

from __future__ import annotations

import datetime
import logging
import zoneinfo
from enum import Enum
from typing import Any, Optional, Union

from dateutil import rrule
from ical.timespan import Timespan
from ical.types.recur import Recur
from pydantic import BaseModel, Field, root_validator, validator

__all__ = [
    "Calendar",
    "Event",
    "DateOrDatetime",
    "EventStatusEnum",
    "EventTypeEnum",
    "VisibilityEnum",
    "ResponseStatus",
    "Attendee",
    "AccessRole",
]

_LOGGER = logging.getLogger(__name__)

DATE_STR_FORMAT = "%Y-%m-%d"
EVENT_FIELDS = (
    "id,iCalUID,summary,start,end,description,location,transparency,status,eventType,"
    "visibility,attendees,attendeesOmitted,recurrence,recurringEventId,originalStartTime"
)
MIDNIGHT = datetime.time()
ID_DELIM = "_"


class AccessRole(str, Enum):
    """The effective access role of the caller."""

    FREE_BUSY_READER = "freeBusyReader"
    """Provides read access to free/busy information."""

    READER = "reader"
    """Provides read access to the calendar."""

    WRITER = "writer"
    """Provides read and write access to the calendar."""

    OWNER = "owner"
    """Provides ownership of the calendar."""

    @property
    def is_writer(self) -> bool:
        """Return if this role can create, delete, update events."""
        return self in (AccessRole.WRITER, AccessRole.OWNER)


class Calendar(BaseModel):
    """Metadata associated with a calendar."""

    id: str
    """Identifier of the calendar."""

    summary: str = ""
    """Title of the calendar."""

    description: Optional[str]
    """Description of the calendar."""

    location: Optional[str]
    """Geographic location of the calendar as free-form text."""

    timezone: Optional[str] = Field(alias="timeZone", default=None)
    """The time zone of the calendar."""

    access_role: AccessRole = Field(alias="accessRole")
    """The effective access role that the authenticated user has on the calendar."""

    selected: bool = False
    """Whether the calendar content shows up in the calendar UI."""

    primary: bool = False
    """Whether the calendar is the primary calendar of the authenticated user."""

    class Config:
        """Pydnatic model configuration."""

        allow_population_by_field_name = True


class DateOrDatetime(BaseModel):
    """A date or datetime."""

    date: Optional[datetime.date] = Field(default=None)
    """The date, in the format "yyyy-mm-dd", if this is an all-day event."""

    date_time: Optional[datetime.datetime] = Field(alias="dateTime", default=None)
    """The time, as a combined date-time value."""

    # Note: timezone is only used for creating new events
    timezone: Optional[str] = Field(alias="timeZone", default=None)
    """The time zone in which the time is specified."""

    @classmethod
    def parse(cls, value: datetime.date | datetime.datetime) -> DateOrDatetime:
        """Create a DateOrDatetime from a raw date or datetime value."""
        if isinstance(value, datetime.datetime):
            return cls(date_time=value)
        return cls(date=value)

    @property
    def value(self) -> Union[datetime.date, datetime.datetime]:
        """Return either a datetime or date representing the Datetime."""
        if self.date is not None:
            return self.date
        if self.date_time is not None:
            if self.date_time.tzinfo is None and self.timezone is not None:
                return self.date_time.replace(tzinfo=zoneinfo.ZoneInfo(self.timezone))
            return self.date_time
        raise ValueError("Datetime has invalid state with no date or date_time")

    def normalize(self, tzinfo: datetime.tzinfo | None = None) -> datetime.datetime:
        """Convert date or datetime to a value that can be used for comparison."""
        value = self.value
        if not isinstance(value, datetime.datetime):
            value = datetime.datetime.combine(value, MIDNIGHT)
        if value.tzinfo is None:
            value = value.replace(tzinfo=(tzinfo if tzinfo else datetime.timezone.utc))
        return value

    @root_validator
    def _check_date_or_datetime(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Validate the date or datetime fields are set properly."""
        if not values.get("date") and not values.get("date_time"):
            raise ValueError("Unexpected missing date or dateTime value")
        # Truncate microseconds for datetime serialization back to json
        if datetime_value := values.get("date_time"):
            if isinstance(datetime_value, datetime.datetime):
                values["date_time"] = datetime_value.replace(microsecond=0)
        elif values.get("timezone"):
            raise ValueError("Timezone with date (only) not supported")
        return values

    class Config:
        """Model configuration."""

        allow_population_by_field_name = True
        arbitrary_types_allowed = True


class EventStatusEnum(str, Enum):
    "Status of the event."

    CONFIRMED = "confirmed"
    """The event is confirmed."""

    TENTATIVE = "tentative"
    """The event is tentatively confirmed."""

    CANCELLED = "cancelled"
    """The event is cancelled (deleted)."""


class EventTypeEnum(str, Enum):
    """Type of the event."""

    DEFAULT = "default"
    """A regular event or not further specified."""

    OUT_OF_OFFICE = "outOfOffice"
    """An out-of-office event."""

    FOCUS_TIME = "focusTime"
    """A focus-time event."""


class VisibilityEnum(str, Enum):
    """Visibility of the event."""

    DEFAULT = "default"
    """Uses the default visibility for events on the calendar."""

    PUBLIC = "public"
    """The event is public and event details are visible to all readers of the calendar."""

    PRIVATE = "private"  # Same as confidential
    """The event is private and only event attendees may view event details."""


class ResponseStatus(str, Enum):
    """The attendee's response status."""

    NEEDS_ACTION = "needsAction"
    """The attendee has not responded to the invitation (recommended for new events)."""

    DECLINED = "declined"
    """The attendee has declined the invitation."""

    TENTATIVE = "tentative"
    """The attendee has tentatively accepted the invitation."""

    ACCEPTED = "accepted"
    """The attendee has accepted the invitation."""


class Attendee(BaseModel):
    """An attendee of an event."""

    id: Optional[str] = None
    """The attendee's Profile ID, if available."""

    email: str = ""
    """The attendee's email address, if available."""

    display_name: Optional[str] = Field(alias="displayName", default=None)
    """The attendee's name, if available."""

    optional: bool = False
    """Whether this is an optional attendee."""

    comment: Optional[str] = None
    """The attendee's response comment."""

    response_status: ResponseStatus = Field(
        alias="responseStatus", default=ResponseStatus.NEEDS_ACTION
    )
    """The attendee's response status."""


class SyntheticEventId:
    """Used to generate a event ids for synthetic recurring events.

    A `gcal_sync.timeline.Timeline` will create synthetic events for each instance
    of a recurring event. The API returns the original event id of the underlying
    event as `recurring_event_id`. This class is used to create the synthetic
    unique `event_id` that includes the date or datetime value of the event instance.

    This class does not generate values in the `recurring_event_id` field.
    """

    def __init__(
        self, event_id: str, dtstart: datetime.date | datetime.datetime
    ) -> None:
        self._event_id = event_id
        self._dtstart = dtstart

    @classmethod
    def of(  # pylint: disable=invalid-name]
        cls,
        event_id: str,
        dtstart: datetime.date | datetime.datetime,
    ) -> SyntheticEventId:
        """Create a SyntheticEventId based on the event instance."""
        return SyntheticEventId(event_id, dtstart)

    @classmethod
    def parse(cls, synthetic_event_id: str) -> SyntheticEventId:
        """Parse a SyntheticEventId from the event id string."""
        parts = synthetic_event_id.rsplit(ID_DELIM, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(
                f"id was not a valid synthetic_event_id: {synthetic_event_id}"
            )
        dtstart: datetime.date | datetime.datetime
        if len(parts[1]) != 8:
            if len(parts[1]) == 0 or parts[1][-1] != "Z":
                raise ValueError(
                    f"SyntheticEventId had invalid date/time or timezone: {synthetic_event_id}"
                )

            dtstart = datetime.datetime.strptime(
                parts[1][:-1], "%Y%m%dT%H%M%S"
            ).replace(tzinfo=datetime.timezone.utc)
        else:
            dtstart = datetime.datetime.strptime(parts[1], "%Y%m%d").date()
        return SyntheticEventId(parts[0], dtstart)

    @classmethod
    def is_valid(cls, synthetic_event_id: str) -> bool:
        """Return true if the value is a valid SyntheticEventId string."""
        try:
            cls.parse(synthetic_event_id)
        except ValueError:
            return False
        return True

    @property
    def event_id(self) -> str:
        """Return the string value of the new event id."""
        if isinstance(self._dtstart, datetime.datetime):
            utc = self._dtstart.astimezone(datetime.timezone.utc)
            return f"{self._event_id}{ID_DELIM}{utc.strftime('%Y%m%dT%H%M%SZ')}"
        return f"{self._event_id}{ID_DELIM}{self._dtstart.strftime('%Y%m%d')}"

    @property
    def original_event_id(self) -> str:
        """Return the underlying/original event id."""
        return self._event_id

    @property
    def dtstart(self) -> datetime.date | datetime.datetime:
        """Return the date value for the event id."""
        return self._dtstart


class Event(BaseModel):
    """A single event on a calendar."""

    id: Optional[str] = None
    """Opaque identifier of the event."""

    ical_uuid: Optional[str] = Field(alias="iCalUID", default=None)
    """Event unique identifier as defined in RFC5545.

    Note that the iCalUID and the id are not identical. One difference in
    their semantics is that in recurring events, all occurrences of one event
    have different ids while they all share the same iCalUIDs.
    """

    summary: str = ""
    """Title of the event."""

    start: DateOrDatetime
    """The (inclusive) start time of the event."""

    end: DateOrDatetime
    """The (exclusive) end time of the event."""

    description: Optional[str]
    """Description of the event, which can contain HTML."""

    location: Optional[str]
    """Geographic location of the event as free-form text."""

    transparency: str = Field(default="opaque")
    """Whether the event blocks time on the calendar.

    Will either be `opaque` which means the calendar does block time on the
    calendar or `transparent` which means it does not block time on the calendar.
    """

    status: EventStatusEnum = EventStatusEnum.CONFIRMED
    """Status of the event.

    Note that deleted events are only returned in some scenarios based on request options
    such as enabling incremental sync or explicitly asking for deleted items. That is,
    most use cases should not need to involve checking the status.
    """

    event_type: EventTypeEnum = Field(alias="eventType", default=EventTypeEnum.DEFAULT)
    """Specific type of the event."""

    visibility: VisibilityEnum = VisibilityEnum.DEFAULT
    """Visibility of the event."""

    attendees: list[Attendee] = []
    """The attendees of the event."""

    attendees_omitted: bool = Field(alias="attendeesOmitted", default=False)
    """Whether attendees may have been omitted from the event's representation."""

    recurrence: list[str] = []
    """List of RRULE, EXRULE, RDATE and EXDATE lines for a recurring event.

    See RFC5545 for more details."""

    recurring_event_id: Optional[str] = Field(alias="recurringEventId", default=None)
    """The id of the primary even to which this recurring event belongs."""

    original_start_time: Optional[DateOrDatetime] = Field(
        alias="originalStartTime", default=None
    )
    """A unique identifier for when this event would start in the original recurring event."""

    @property
    def computed_duration(self) -> datetime.timedelta:
        """Return the event duration."""
        return self.end.value - self.start.value

    @property
    def rrule(self) -> rrule.rrule | rrule.rruleset:
        """Return the recurrence rules as a set of rules."""
        try:
            return rrule.rrulestr("\n".join(self.recurrence), dtstart=self.start.value)
        except ValueError as err:
            raise ValueError(
                f"Invalid recurrence rule: {self.json()}: {str(err)}"
            ) from err

    @property
    def recur(self) -> Recur:
        """Build a recurrence rule for the event."""
        if len(self.recurrence) != 1:
            raise ValueError(f"Unexpected recurrence value: {self.recurrence}")
        return Recur.from_rrule(self.recurrence[0])

    @root_validator(pre=True)
    def _allow_cancelled_events(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Special case for canceled event tombstones that are missing required fields."""
        if status := values.get("status"):
            if status == EventStatusEnum.CANCELLED:
                if "start" not in values:
                    values["start"] = DateOrDatetime(date=datetime.date.min)
                if "end" not in values:
                    values["end"] = DateOrDatetime(date=datetime.date.min)
        return values

    @root_validator(pre=True)
    def _adjust_visibility(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Convert legacy visibility types to new types."""
        if visibility := values.get("visibility"):
            if visibility == "confidential":
                values["visibility"] = "private"
        return values

    @validator("recurrence", each_item=True)
    def _validate_rrule_params(cls, rule: str) -> str:
        """Remove rrule property parameters not supported by the dateutil.rrule library."""
        if not rule.startswith("RRULE;"):
            return rule
        right = rule[6:]
        parts = right.split(":", maxsplit=1)
        if len(parts) == 2:
            # Rebuild string without parameters
            return f"RRULE:{parts[1]}"
        return rule  # rrule parser fail

    @root_validator
    def _validate_rrule(cls, values: dict[str, Any]) -> dict[str, Any]:
        """The API returns invalid RRULEs that need to be coerced to valid."""
        # Rules may need updating of start time has a timezone
        if not (recurrence := values.get("recurrence")) or not (
            dtstart := values.get("start")
        ):
            return values
        values["recurrence"] = [cls._adjust_rrule(rule, dtstart) for rule in recurrence]
        return values

    @classmethod
    def _adjust_rrule(cls, rule: str, dtstart: DateOrDatetime) -> str:
        """Apply fixes to the rrule."""
        if not rule.startswith("RRULE:"):
            return rule

        parts = {}
        for part in rule[6:].split(";"):
            if "=" not in part:
                raise ValueError(
                    f"Recurrence rule had unexpected format missing '=': {rule}"
                )
            key, value = part.split("=", 1)
            key = key.upper()
            parts[key.upper()] = value

        if not (until := parts.get("UNTIL")):
            return rule

        until_parts = until.split("T")
        if len(until_parts) > 2:
            raise ValueError(f"Recurrence rule had invalid UNTIL: {rule}")

        if dtstart.date_time:
            if dtstart.date_time.tzinfo and len(until_parts) == 1:
                # UNTIL is a DATE but must be a DATE-TIME
                parts["UNTIL"] = f"{until}T000000Z"
            elif dtstart.date_time.tzinfo is None and until_parts[1].endswith("Z"):
                # Date should be floating
                parts["UNTIL"] = f"{until_parts[0]}T{until_parts[1][:-1]}"
        elif dtstart.date:
            if len(until_parts) > 1:
                # UNTIL is a DATE-TIME but must be a DATE
                parts["UNTIL"] = until_parts[0]

        rule = ";".join(f"{k}={v}" for k, v in parts.items())
        try:
            rrule.rrulestr(rule, dtstart=dtstart.value)
        except ValueError as err:
            raise ValueError(
                f"Invalid recurrence rule {rule} for {dtstart}: {str(err)}"
            ) from err
        return rule

    @property
    def timespan(self) -> Timespan:
        """Return a timespan representing the event start and end."""
        return self.timespan_of(datetime.timezone.utc)

    def timespan_of(self, tzinfo: datetime.tzinfo | None = None) -> Timespan:
        """Return a timespan representing the event start and end."""
        if tzinfo is None:
            tzinfo = datetime.timezone.utc
        return Timespan.of(
            self.start.normalize(tzinfo),
            self.end.normalize(tzinfo),
        )

    def intersects(self, other: "Event") -> bool:
        """Return True if this event overlaps with the other event."""
        return self.timespan.intersects(other.timespan)

    def includes(self, other: "Event") -> bool:
        """Return True if the other event starts and ends within this event."""
        return self.timespan.includes(other.timespan)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timespan < other.timespan

    def __gt__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timespan > other.timespan

    def __le__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timespan <= other.timespan

    def __ge__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timespan >= other.timespan

    class Config:
        """Model configuration."""

        allow_population_by_field_name = True
