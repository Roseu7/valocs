import datetime
import time
import calendar

def convert_date(date_string: str) -> int:
    if len(date_string) != 8:
        raise ValueError(":warning:開始時間は8桁の文字列である必要があります")

    try:
        month = int(date_string[:2])
        day = int(date_string[2:4])
        hour = int(date_string[4:6])
        minute = int(date_string[6:])
    except ValueError:
        raise ValueError(":warning:開始時間は数字のみである必要があります")

    if not 1 <= month <= 12:
        raise ValueError(":warning:開始時間の月は1から12の間である必要があります")
    if not 1 <= day <= 31:
        raise ValueError(":warning:開始時間の日は1から31の間である必要があります")
    if not 0 <= hour <= 23:
        raise ValueError(":warning:開始時間の時間は0から23の間である必要があります")
    if not 0 <= minute <= 59:
        raise ValueError(":warning:開始時間の分は0から59の間である必要があります")

    now = datetime.datetime.now()
    current_year = now.year

    _, max_days = calendar.monthrange(current_year, month)
    if day > max_days:
        raise ValueError(f":warning:{month}月の日付は1から{max_days}の間である必要があります")

    try:
        date_obj = datetime.datetime(current_year, month, day, hour, minute)
    except ValueError as e:
        raise ValueError(f":warning:無効な日付です: {e}")

    if date_obj < now:
        date_obj = datetime.datetime(current_year + 1, month, day, hour, minute)

    unix_time = int(time.mktime(date_obj.timetuple()))

    return unix_time