#!/usr/bin/env python
#-*- coding:utf-8 -*-

"""
    *.py: Description of what * does.
    Last Modified:
"""

__author__ = "Sathappan Muthiah"
__email__ = "sathap1@vt.edu"
__version__ = "0.0.1"

from .dbManager import SQLiteWrapper
import pandas as pd
from . import GeoPoint


class BaseGazetteer(object):
    """
    Base Gazetteer class
    """
    def __init__(self):
        pass

    def query(self, name, absoluteMatch=False):
        """
        search for name
        """
        pass


class GeoNames(BaseGazetteer):
    def __init__(self, dbpath, dbname, tables=None, priority=None):
        self.db = SQLiteWrapper(dbpath)
        self.dbname = dbname
        if tables is None:
            self.tables = ('allCities', 'allAdmins', 'allCountries')
        else:
            self.tables = tables

        if priority is None:
            # TODO: Define default priority
            self.priority = None
        else:
            self.priority = priority

    def query(self, name, min_popln=0):
        """
        Search the locations DB for the given name
        params:
        name - string
        absoluteMatch - boolean. If False, pick the longest substring that returns a
                        match

        return:
        list of possible locations the input string refers to
        """
        #res = self.db.query(u"SELECT * FROM geonames_fullText WHERE name='{0}' OR admin1='{0}' or country='{0}'".format(name))
        #result[t] = self.score([dict(r) for r in res for i in r])

        country = self._querycountry(name)
        if country == []:
            admin = self._querystate(name)
            city = self._querycity(name, min_popln=min_popln)
        else:
            admin = []
            city = []
        #res = self.score(city, admin, country)
        ldist = (city + country + admin)
        if city == [] and country == [] and admin == []:
            #ipdb.set_trace()
            ldist = self._query_alternatenames(name, min_popln)
            ## For examples like new york city's times square
            if ldist == [] and "'" in name:
                ldist = self._query_alternatenames(name.split("'", 1)[0])

        return ldist

    def _querycountry(self, name):
        """
        Check if name is a country name
        """
        return self.db.query(u"""SELECT *, 'country' as 'ltype'
                             FROM allcountries WHERE country="{0}" """.format(name))

    def _querystate(self, name):
        """
        Check if name is an admin name
        """
        stmt = u"""SELECT a.geonameid, a.name as 'admin1', b.country as 'country',
                   'admin' as 'ltype' FROM
                alladmins as a, allcountries as b WHERE (name="{0}" or asciiname="{0}")
                and substr(a.key, 0, 3)=b.ISO""".format(name)
        return self.db.query(stmt)

    def _querycity(self, name, min_popln=0):
        """
        Check if name is a city name
        """
        stmt = u"""SELECT a.id as geonameid, a.name as 'city',
               a.population,a.latitude, a.longitude, c.country as 'country',
               b.name as 'admin1', 'city' as 'ltype',
               a.featureCode, a.cc2 as 'countryCode'
               FROM allcities a, alladmins b, allcountries c WHERE
               (a.name="{0}" or a.asciiname="{0}") and a.population >= {1}
               and a.countryCode=c.ISO and a.countryCode||'.'||a.admin1 = b.key""".format(name, min_popln)
        res = self.db.query(stmt)
        if res:
            df = pd.DataFrame([i.__dict__ for i in res])
            if any(df["ltype"] == "city") and any(df["ltype"] != "city"):
                df = df[df["ltype"] == "city"]

            df['population'] = (df['population'] + 1).astype(float)
            df['confidence'] = (df['population']) / (2 * (df['population'].sum())) + 0.5

            res = [GeoPoint(**d) for d in df.to_dict(orient='records')]

        return res

    def _query_alternatenames(self, name, min_popln=0):
        """
        check if name matches alternate name
        """
        stmt = u"""SELECT a.id as geonameid, a.name as 'city', a.population,
                'city' as 'ltype', a.latitude, a.longitude, c.country as country, b.name as admin1
                FROM alternatenames as d,
                allcities as a, alladmins as b,
                allcountries as c WHERE a.id=d.geonameId and alternatename="{0}"
                and a.countryCode=c.ISO and a.countryCode||'.'||a.admin1 = b.key and
                a.population >= {1}""".format(name, min_popln)
        res = self.db.query(stmt)
        if res:
            df = pd.DataFrame([i.__dict__ for i in res])
            #if any(df["ltype"] == "city"):
            #    df = df[df["ltype"] == "city"]
            df['population'] = (df['population'] + 1).astype(float)
            df['confidence'] = (df['population']) / (2 * (df['population'].sum())) + 0.5

            res = [GeoPoint(**d) for d in df.to_dict(orient='records')]

        return res

    def get_locInfo(self, locId):
        """
        return full loc tuple of name, admin1 name, country, population,
        longitude etc.
        """
        stmt = u"""SELECT a.id as geonameid, a.name as 'city',
               a.population,a.latitude, a.longitude, c.country as 'country',
               b.name as 'admin1', 'city' as 'ltype',
               a.featureCode, a.cc2 as 'countryCode'
               FROM allcities a, alladmins b, allcountries c WHERE
               a.id='{}' and a.countryCode=c.ISO and
               a.countryCode||'.'||a.admin1 = b.key""".format(locId)

        return self.db.query(stmt)

    def get_country(self, cc2):
        return self.db.query("SELECT * FROM allcountries where ISO='{}'".format(cc2))