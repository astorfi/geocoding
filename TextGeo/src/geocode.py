#!/usr/bin/env python
#-*- coding:utf-8 -*-

"""
    *.py: Description of what * does.
    Last Modified:
"""

__author__ = "Sathappan Muthiah"
__email__ = "sathap1@vt.edu"
__version__ = "0.0.1"

from workerpool import WorkerPool
from geoutils.gazetteer_mod import GeoNames
from geoutils.dbManager import ESWrapper
from collections import defaultdict
from urlparse import urlparse
from geoutils import LocationDistribution
import logging
from geoutils import encode, isempty
import json
import ipdb
import re

numstrip=re.compile("\d")
tracer = logging.getLogger('elasticsearch')
tracer.setLevel(logging.CRITICAL)  # or desired level
tracer = logging.getLogger('urllib3')
tracer.setLevel(logging.CRITICAL)  # or desired level
# tracer.addHandler(logging.FileHandler('indexer.log'))
logging.basicConfig(filename='geocode.log', level=logging.DEBUG)
log = logging.getLogger("rssgeocoder")


class BaseGeo(object):
    def __init__(self, db, min_popln=0, min_length=1):
        self.gazetteer = GeoNames(db)
        self.min_popln = min_popln
        self.min_length = min_length
        self.weightage = {
            "LOCATION": 1.0,
            "NATIONALITY": 0.75,
            "ORGANIZATION": 0.5,
            "OTHER": 0.2
        }

    def geocode(self, doc=None, loclist=None, **kwargs):
        locTexts = []
        if doc is not None:
            # Get all location entities from document with atleast min_length characters
            locTexts += [(numstrip.sub("", l['expr'].lower()).strip(), l['neType']) for l in
                         doc["BasisEnrichment"]["entities"]
                         if ((l["neType"] in ("LOCATION", "NATIONALITY")) and
                             len(l['expr']) >= self.min_length)]

            # locTexts += [(numstrip.sub("", l['expr'].lower()).strip(), 'OTHER') for l in
            #              doc['BasisEnrichment']['nounPhrases']]

        if loclist is not None:
            locTexts += [l.lower() for l in loclist]

        results = self.get_locations_fromURL((doc["url"] if doc.get("url", "")
                                              else doc.get("link", "")))
        # results = {}
        # kwargs['analyzer'] = 'standard'
        return self.geocode_fromList(locTexts, results, **kwargs)

    def geocode_fromList(self, locTexts, results=None, min_popln=None, **kwargs):
        if results is None:
            results = {}

        if min_popln is None:
            min_popln = self.min_popln

        itype = {}
        for l in locTexts:
            if l == "":
                continue
            if isinstance(l, tuple):
                itype[l[0]] = l[1]
                l = l[0]
            else:
                itype[l] = 'LOCATION'
            try:
                if l in results:
                    results[l].frequency += 1
                else:
                    for sub in l.split(","):
                        sub = sub.strip()
                        if sub in results:
                            results[sub].frequency += 1
                        else:
                            itype[sub] = itype[l]
                            try:
                                # # Exclusion
                                # list_exclude = ['city','town']
                                # for ext in list_exclude:
                                #     if ext in sub:
                                #         sub = sub.replace(ext, "")
                                query = self.gazetteer.query(sub, min_popln=min_popln,**kwargs)
                                results[sub] = LocationDistribution(query)
                            except UnicodeDecodeError:
                                ipdb.set_trace()
                            results[sub].frequency = 1
            except UnicodeDecodeError:
                log.exception("Unable to make query for string - {}".format(encode(l)))

        scores = self.score(results)
        custom_max = lambda x: max(x.viewvalues(),
                                   key=lambda y: y['score'])
        lrank = self.get_locRanks(scores, results)
        lmap = {l: custom_max(lrank[l]) for l in lrank if not lrank[l] == {}}
        total_weight = sum([self.weightage[itype.get(key, 'OTHER')] for key in lmap])
        return lmap, max(lmap.items(),
                         key=lambda x: x[1]['score'] * self.weightage[itype.get(x[0], 'OTHER')] / total_weight)[1]['geo_point'] if scores else {}

    def get_locations_fromURL(self, url):
        """
        Parse URL to get URL COUNTRY and also URL SUBJECT like taiwan in
        'cnn.com/taiwan/protest.html'

        Params:
            url - a web url

        Returns:
            Dict of locations obtained from URL
        """
        results = {}
        urlinfo = urlparse(url)
        if urlinfo.netloc != "":
            urlsubject = urlinfo.path.split("/", 2)[1]
            urlcountry = urlinfo.netloc.rsplit(".", 1)[-1]
            # Find URL DOMAIN Country from 2 letter iso-code
            if len(urlcountry.strip()) == 2:
                urlcountry = self.gazetteer.get_country(urlcountry.upper())
                if urlcountry != []:
                    urlcountry = urlcountry[0]
                    urlcountry.confidence = 1.0
                    results["URL-DOMAIN_{}".format(urlcountry)] = LocationDistribution(urlcountry)
                    results["URL-DOMAIN_{}".format(urlcountry)].frequency = 1

            if 5 < len(urlsubject) < 20:
                usubj_q = self.gazetteer.query(urlsubject, 15000)
                if usubj_q:
                    results["URL-SUBJECT_{}".format(urlsubject)] = LocationDistribution(usubj_q)
                    results["URL-SUBJECT_{}".format(urlsubject)].frequency = 1
        return results

    def annotate(self, doc, **kwargs):
        """
        Attach embersGeoCode to document
        """
        try:
            lmap, gp = self.geocode(doc=doc, **kwargs)
        except UnicodeDecodeError as e:
            log.exception("unable to geocode:{}".format(str(e)))
            lmap, gp = {}, {}

        doc['embersGeoCode'] = gp
        doc["location_distribution"] = lmap
        return doc

    def update(self,l,scoresheet):
        for s in l.city:
            scoresheet[s] += l.city[s] * l.frequency
        for s in l.admin1:
            scoresheet[s] += l.admin1[s] * l.frequency
        for s in l.country:
            scoresheet[s] += l.country[s] * l.frequency

    def score(self, results):
        scoresheet = defaultdict(float)
        num_mentions = float(sum((l.frequency for l in results.values())))

        _ = [self.update(item,scoresheet) for item in results.viewvalues()]
        for s in scoresheet:
            scoresheet[s] /= num_mentions

        return scoresheet

    def get_realization_score(self,l,scores):
        lscore_map = {}
        for lstr, r in l.realizations.viewitems():
            base_score = scores[lstr]
            # if r.ltype == 'city':
            if not isempty(r.city):
                l_adminstr = '/'.join([r.country, r.admin1, ''])
                base_score = (base_score + scores[l_adminstr] + scores[r.country + "//"]) * r.confidence

            elif not isempty(r.admin1):
                base_score = (base_score + scores[r.country + "//"]) * r.confidence

            elif r.ltype == "country":
                # do nothing
                pass
            else:
                base_score = base_score * r.confidence
                # code for other types
                # if not isempty(r.city):
                #    l_adminstr = '/'.join([r.country, r.admin1, ''])
                #    base_score = (base_score + scores[l_adminstr] + scores[r.country + "//"]) * r.confidence

                # ipdb.set_trace()
                # raise Exception("Unknown location type-{} for {}".format(r.ltype, lstr))

            lscore_map[lstr] = {'score': base_score, 'geo_point': r.__dict__}

        # for s in l.realizations:
        #    base_score = scores[s]
        #    if l.realizations[s].ltype not in ('country', 'admin'):
        #        l_adminstr = encode('/'.join([l.realizations[s].country,
        #                               l.realizations[s].admin1, '']))

        #        base_score += scores[l_adminstr] + scores[l.realizations[s].country]

        #    elif l.realizations[s].ltype == 'admin':
        #        base_score += scores[l.realizations[s].country]

        #    lscore_map[s] = {'score': base_score, 'geo_point': l.realizations[s].__dict__}
        return lscore_map

    def get_locRanks(self, scores, loc_cand):
        """
        Each city score needs to be re-inforced with the
        corresponding state and country scores to get the actual meaning
        of that name. For example, several mentions of cities within virginia
        would have given virginia
        state a high score. Now this high score has to be brought back to lower levels to
        decide on meaning of each name/city
        """
        loc_rankmap = {}

        for locpt in loc_cand:
            loc_rankmap[locpt] = self.get_realization_score(loc_cand[locpt],scores)
        return loc_rankmap


# class TextGeo(object):
#     def __init__(self, dbpath="./Geonames_dump.sql", min_popln=0, coverageLength=10):
#         """
#         Description
#         """
#         self.coverageLength = coverageLength
#         self.gazetteer = GeoNames("./Geonames_dump.sql")
#         self.min_popln = min_popln
#
#     def geocode(self, doc):
#         """
#
#         """
#         def getEntityDetails(entity):
#             """
#             return entity string, starting offset, coverage end point
#             """
#             start, end = entity['offset'].split(":")
#             start, end = int(start), int(end)
#             return (entity['expr'], start,
#                     start - self.coverageLength,
#                     end + self.coverageLength)
#
#         urlinfo = urlparse(doc["url"])
#         loc_results = {}
#         locTexts = [getEntityDetails(l) for l in doc["BasisEnrichment"]['entities']
#                     if l['neType'] == 'LOCATION']
#         if urlinfo.netloc != "":
#             urlsubject = urlinfo.path.split("/", 2)[1]
#             urlcountry = urlinfo.netloc.rsplit(".", 1)[-1]
#             if len(urlcountry.strip()) == 2:
#                 urlcountry = self.gazetteer.get_country(urlcountry.upper())
#                 if urlcountry != []:
#                     urlcountry = urlcountry[0]
#                     urlcountry.confidence = 1.0
#                     loc_results["url"] = LocationDistribution(urlcountry)
#                     loc_results["url"].frequency = 1
#             if len(urlsubject) < 20:
#                 locTexts.insert(0, (urlsubject, -1, -1, -1))
#
#         loc_results.update(self.query_gazetteer(self.group(locTexts)))
#
#         scores = self.score(loc_results)
#         custom_max = lambda x: max(x.realizations.viewvalues(),
#                                    key=lambda x: scores[x.__str__()])
#         lmap = {l: custom_max(loc_results[l]['geo-point']) for l in loc_results
#                 if not loc_results[l]['geo-point'].isEmpty()}
#         egeo = {}
#         if scores:
#             egeo = scores[max(scores, key=lambda x: scores[x])]
#         return lmap, egeo
#
#     def score(self, results):
#         scoresheet = defaultdict(float)
#
#         def update(item):
#             l = item['geo-point']
#             freq = item['frequency']
#             for s in l.city:
#                 scoresheet[s] += l.city[s] * freq
#             for s in l.admin1:
#                 scoresheet[s] += l.admin1[s] * freq
#             for s in l.country:
#                 scoresheet[s] += l.country[s] * freq
#
#         [update(item) for item in results.viewvalues()]
#         return scoresheet
#
#     def query_gazetteer(self, lgroups):
#         """
#         get Location groups
#         """
#         gp_map = {}
#         query_gp = lambda x: self.gazetteer.query(x) if x not in gp_map else gp_map[x]
#         for grp in lgroups:
#             imap = {txt: query_gp(txt) for txt in grp}
#             imap = self.get_geoPoints_intersection(imap)
#             for l in imap:
#                 if l in gp_map:
#                     gp_map[l]['frequency'] += 1
#                 else:
#                     gp_map[l] = {'geo-point': imap[l], 'frequency': 1}
#
#             #gp_map.update(imap)
#
#         for l in gp_map:
#             gp_map[l]['geo-point'] = LocationDistribution(gp_map[l]['geo-point'])
#
#         return gp_map
#
#     def group(self, loc):
#         groups = []
#         i = 0
#         while i < len(loc):
#             grp = [loc[i][0]]
#             for j, l in enumerate(loc[i + 1:]):
#                 if l[1] <= loc[i][-1]:
#                     grp.append(l[0])
#                     i += 1
#                 else:
#                     groups.append(grp)
#                     i += 1
#                     grp = [loc[i][0]]
#                     break
#             else:
#                 groups.append(grp)
#                 i += 1
#         return groups
#
#     def get_geoPoints_intersection(self, gps):
#         try:
#             selcountry = set.intersection(*[set([l.country])
#                                             for name in gps for l in gps[name]])
#         except:
#             selcountry = None
#
#         if not selcountry:
#             return gps
#
#         selcountry = selcountry.pop()
#         filtered_gps = [set([encode('/'.join([l.country, l.admin1, ""]))]) for name in gps
#                         for l in gps[name] if l.country == selcountry]
#
#         sel_admin1 = set.intersection(*filtered_gps)
#         if not sel_admin1:
#             return {name: [l for l in gps[name] if l.country == selcountry]
#                     for name in gps}
#
#         sel_admin1 = sel_admin1.pop()
#         ns = {}
#         for l in gps:
#             t_admin = [gp for gp in gps[l] if gp.__str__() == sel_admin1]
#             if t_admin != []:
#                 ns[l] = t_admin
#                 continue
#             t_cand = [gp for gp in gps[l]
#                       if encode("/".join([gp.country, gp.admin1, ""])) == sel_admin1]
#             ns[l] = t_cand
#         return ns


def tmpfun(doc):
    try:
        msg = json.loads(doc)
        msg = GEO.annotate(msg)
        return msg
    except Exception, e:
        print("error", str(e))


if __name__ == "__main__":
    import sys
    import argparse
    import os
    import time
    from geoutils import smart_open
    from joblib import Parallel, delayed
    parser = argparse.ArgumentParser()
    parser.add_argument("--cat", "-c", action='store_true',
                        default=False, help="read from stdin")
    parser.add_argument("-i", "--infile", type=str, default=os.path.expanduser('~/MANSAgsr_BASIS_Enriched.json'), help="input file")
    parser.add_argument("-o", "--outfile", type=str, default=os.path.expanduser('~/out.json'),help="output file")
    parser.add_argument("-p", "--parallel", type=bool, default=True)
    args = parser.parse_args()

    db = ESWrapper(index_name="geonames", doc_type="places")
    GEO = BaseGeo(db)

    if args.cat:
        infile = sys.stdin
        outfile = sys.stdout
    else:
        infile = smart_open(args.infile)
        outfile = smart_open(args.outfile, "wb")

    lno = 0
    t1 = time.time()
    if args.parallel:

        ################
        ### Method-1 ###
        ################
        wp = WorkerPool(infile, outfile, tmpfun, 200)
        wp.run()

        # ################
        # ### Method-2 ###
        # ################
        # articles = Parallel(n_jobs=1, verbose=10)(delayed(tmpfun)(ln) for ln in infile)
        # with io.open(args.outfile, 'wb', encoding='utf8') as outfile:
        #     for ln in articles:
        #         # Convert Python Object (Dict) to JSON
        #         str_ = json.dumps(ln, sort_keys=True, ensure_ascii=False)
        #         outfile.write(to_unicode(str_) + "\n")


    else:

        for l in infile:
            try:
                j = json.loads(l)
                j = GEO.annotate(j)
                #log.debug("geocoded line no:{}, {}".format(lno,
                #                                           encode(j.get("link", ""))))
                lno += 1
                outfile.write(encode(json.dumps(j, ensure_ascii=False) + "\n"))
            except UnicodeEncodeError:
                log.exception("Unable to readline")
                continue

    t2 = time.time()
    passed_time = t2 - t1
    print('Time duration: ', passed_time)

    if not args.cat:
        infile.close()
        outfile.close()

    exit(0)
