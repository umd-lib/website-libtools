import requests
import os
import furl
import logging
import zoneinfo

from requests_oauthlib import OAuth2Session
from oauthlib.oauth2 import BackendApplicationClient
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from cachetools import cached, TTLCache
from datetime import datetime

app = FastAPI()
libapps = FastAPI()

load_dotenv('.env')

env = {}
for key in ('LIBAPPS_BASE',
            'LIBAPPS_CLIENT',
            'LIBAPPS_SECRET'):
    env[key] = os.environ.get(key)
    if env[key] is None:
        raise RuntimeError(f'Missing environment variable: {key}')

libapps_api = furl.furl(env['LIBAPPS_BASE'])
libapps_client = env['LIBAPPS_CLIENT']
libapps_secret = env['LIBAPPS_SECRET']

logger = logging.getLogger('website-libtools')

est = zoneinfo.ZoneInfo('US/Eastern')

@cached(cache=TTLCache(maxsize=2, ttl=3500))
def get_token():
    client = BackendApplicationClient(client_id=libapps_client)
    oauth = OAuth2Session(client=client)
    oauth_endpoint = libapps_api.url + '/oauth/token'
    token = oauth.fetch_token(oauth_endpoint,
                              client_id=libapps_client,
                              client_secret=libapps_secret)
    if token['access_token'] is None:
        return None
    return token['access_token']


def authenticate():
    token = get_token()
    if token is None:
        get_token.cache.clear()

    return token


def make_api_request(url, params=None):
    token = authenticate()
    if token is None:
        return None
    headers = {"Authorization": f"Bearer {token}",
               'Accept': 'application/json',
               'Content-Type': 'application/json'}
    response = requests.get(url, headers=headers, params=params)
    return response


@cached(cache=TTLCache(maxsize=32, ttl=3600))
def get_locations(location=None):
    locations_endpoint = libapps_api.url + '/space/locations'
    params = {'details': '0'}
    response = make_api_request(locations_endpoint, params)

    if response.json() is None:
        return error_response('No Response', 400)
    if location is None:
        return response.json()

    items = response.json()
    if items is not None:
        if location is None:
            return items
        for item in items:
            if item['name'] is not None:
                name = item['name'].lower()
                if location.lower() in name:
                    return item
    return error_response('No Response', 400)


def get_location_hours(lid, query_date):
    hours_endpoint = libapps_api.url + '/hours/' + str(lid)
    params = {'from': query_date}
    response = make_api_request(hours_endpoint, params)
    if response.content is None:
        return error_response('Response is empty')
    if 'application/json' in response.headers.get('Content-Type', ''):
        return response.json()
    return None


@cached(cache=TTLCache(maxsize=32, ttl=60))
def get_location_details(lid, availability):
    spaces_endpoint = libapps_api.url + '/space/items/' + str(lid)
    # params = {'page_size': '100', 'availability': '2025-03-03'}
    if availability is None:
        params = {'page_size': '100'}
    elif availability == 'next':
        params = {'page_size': '100', 'availability': 'next_only'}
    elif availability == 'full':
        # now = datetime.now()
        cur = datetime.now(est)
        from_date = cur.strftime("%Y-%m-%d")
        params = {'page_size': '100', 'availability': from_date}

    response = make_api_request(spaces_endpoint, params)

    logger.debug('Det not cached')
    if response.content is None:
        return error_response('Response is empty')
    if 'application/json' in response.headers.get('Content-Type', ''):
        return response.json()
    return None


@app.get("/")
def read_root():
    return True


def app_locations(location=None):
    return get_locations(location)


def app_spaces(location=None, availability='next'):
    items = get_locations(location)
    spaces = []
    # Check if only one return
    if items['lid'] is not None:
        space = get_location_details(items['lid'], availability)
        if space is not None:
            spaces.append(space)
    else:
        for item in items:
            if item['lid'] is not None:
                space = get_location_details(item['lid'], availability)
                if space is not None:
                    spaces.append(space)
    return spaces


def app_hours(lid):
    cur = datetime.now(est)
    from_date = cur.strftime("%Y-%m-%d")
    locs = get_location_hours(lid, from_date)
    response = {}
    for hr in locs:
        if hr is None:
            return None
        if 'dates' in hr:
            dates = hr['dates']
            if from_date in dates:
                date = dates[from_date]
                if 'status' in date:
                    response['status'] = date['status']
                if 'hours' in date:
                    hours = date['hours']
                    for hour in hours:
                        if 'from' in hour:
                            response['hours_from'] = hour['from']
                        if 'to' in hour:
                            response['hours_to'] = hour['to']
    return response


def build_space_response(locations):
    response = {}
    current_group = None
    current_subsec = {}
    current_avail = 0
    current_total = 0
    current_next_avail = None
    overall_avail = 0
    overall_total = 0
    for spaces in locations:
        for space in spaces:
            if 'groupId' in space and current_group != space['groupId']:
                # Consider refactoring. This does not cover last item
                # in array.
                if len(current_subsec) > 0:
                    current_subsec['next_available'] = current_next_avail
                    response[current_group] = current_subsec
                    current_subsec = {}
                    current_avail = 0
                    current_total = 0
                    current_next_avail = None
                current_group = str(space['groupId'])
                current_subsec['name'] = space['groupName']
                # If they come out of order, use existing array
                if current_group in response:
                    current_subsec = response[current_group]
                    current_avail = int(current_subsec['available'])
                    current_total = int(current_subsec['total'])
                    current_next_avail = current_subsec['next_available']
            if 'availability' in space:
                for sp in space['availability']:
                    if 'to' in sp:
                        to_date = sp['to']
                    if 'from' in sp:
                        from_date = sp['from']
                    current_total = current_total + 1
                    overall_total = overall_total + 1
                    if check_if_available(from_date, to_date):
                        overall_avail = overall_avail + 1
                        current_avail = current_avail + 1
                    current_subsec['available'] = str(current_avail)
                    current_subsec['total'] = str(current_total)
                    if current_next_avail is None:
                        current_next_avail = from_date
                    elif compare_dates(current_next_avail, from_date):
                        current_next_avail = from_date

    # Add last item to array
    if len(current_subsec) > 0:
        current_subsec['next_available'] = current_next_avail
        response[current_group] = current_subsec

    response['overall_available'] = overall_avail
    response['total'] = overall_total
    return response


def check_if_available(from_date, to_date):
    to_date = datetime.fromisoformat(to_date)
    from_date = datetime.fromisoformat(from_date)
    cur = datetime.now(est)

    return from_date < cur < to_date


def compare_dates(date_1, date_2):
    time_1 = datetime.fromisoformat(date_1)
    time_2 = datetime.fromisoformat(date_2)
    return time_1 > time_2


@libapps.get('/mckeldin/details')
def app_mckeldin_raw(availability='full'):
    return app_spaces('Mckeldin', availability)


@libapps.get('/mckeldin/availability')
def app_mckeldin_spaces():
    spaces = app_mckeldin_raw('next')
    return build_space_response(spaces)


@libapps.get('/mckeldin/hours/today')
@cached(cache=TTLCache(maxsize=32, ttl=360))
def app_mckeldin_hours_today():
    lid = '13231'
    hours = app_hours(lid)
    logger.debug("Not Cached")
    return hours


@libapps.get('/stem/details')
def app_stem_raw(availability='full'):
    spaces = app_spaces('Stem', availability)
    return spaces


@libapps.get('/stem/availability')
def app_stem_spaces():
    spaces = app_stem_raw('next')
    return build_space_response(spaces)


@libapps.get('/stem/hours/today')
@cached(cache=TTLCache(maxsize=8, ttl=360))
def app_stem_hours_today():
    lid = '17168'
    hours = app_hours(lid)
    return hours


@libapps.get('/makerspace/hours/today')
@cached(cache=TTLCache(maxsize=8, ttl=360))
def app_makerspace_hours_today():
    lid = '25040'
    hours = app_hours(lid)
    return hours


@libapps.get('/mspal/details')
def app_mspal_raw(availability='full'):
    return app_spaces('Michelle Smith', availability)


@libapps.get('/mspal/availability')
def app_mspal_spaces():
    spaces = app_mspal_raw('next')
    return build_space_response(spaces)


@libapps.get('/mspal/hours/today')
@cached(cache=TTLCache(maxsize=8, ttl=360))
def app_mspal_hours_today():
    lid = '17167'
    hours = app_hours(lid)
    return hours


@libapps.get('/art/details')
def app_art_raw(availability='full'):
    return app_spaces('Art Library', availability)


@libapps.get('/art/availability')
def app_art_spaces():
    spaces = app_art_raw('next')
    return build_space_response(spaces)


@libapps.get('/art/hours/today')
@cached(cache=TTLCache(maxsize=8, ttl=360))
def app_art_hours_today():
    lid = '17166'
    hours = app_hours(lid)
    return hours


def error_response(message='Response error', status=500):
    raise HTTPException(status_code=status, detail=message)


app.mount("/api/libtools", libapps)
