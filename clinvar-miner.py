#!/usr/bin/env python3

import gzip
import re
from collections import OrderedDict
from datetime import datetime
from db import DB
from flask import Flask
from flask import Response
from flask import abort
from flask import redirect
from flask import render_template
from flask import request
from hashlib import sha256
from os import environ
from urllib.parse import urlparse, quote
from werkzeug.contrib.cache import FileSystemCache
from werkzeug.contrib.cache import NullCache
from werkzeug.routing import BaseConverter

app = Flask(__name__)
ttl = float(environ.get('TTL', 0)) #zero means infinity to the FileSystemCache
cache = FileSystemCache('/tmp/clinvar-miner', threshold=1000000) if ttl >= 0 else NullCache()
cache.clear() #delete the cache when the webserver is restarted

app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

#it's necessary to double-escape slashes because WSGI decodes them before passing the URL to Flask
class SuperEscapedConverter(BaseConverter):
    @staticmethod
    def to_python(value):
        return value.replace('%2F', '/')

    @staticmethod
    def to_url(value):
        return quote(value).replace('/', '%252F')

app.url_map.converters['superescaped'] = SuperEscapedConverter

nonstandard_significance_term_map = dict(map(
    lambda line: line[0:-1].split('\t'),
    open('nonstandard_significance_terms.tsv')
))

def get_breakdown_by_condition_and_significance(total_variants_by_condition,
                                                total_variants_by_condition_and_significance):
    breakdown = OrderedDict()
    significances = set()

    for row in total_variants_by_condition:
        condition_name = row['condition_name']
        count = row['count']

        breakdown[condition_name] = {'total': count}

    for row in total_variants_by_condition_and_significance:
        condition_name = row['condition_name']
        significance = row['significance']
        count = row['count']

        breakdown[condition_name][significance] = count
        significances.add(significance)

    #sort alphabetically to be consistent if there are two or more unranked significance terms
    significances = sorted(significances)

    #sort by rank
    significances = sorted(significances, key=significance_rank)

    return breakdown, significances

def get_breakdown_by_gene_and_significance(total_variants_by_gene,
                                           total_variants_by_gene_and_significance):
    breakdown = OrderedDict()
    significances = set()

    for row in total_variants_by_gene:
        gene = row['gene']
        count = row['count']

        breakdown[gene] = {'total': count}

    for row in total_variants_by_gene_and_significance:
        gene = row['gene']
        significance = row['significance']
        count = row['count']

        breakdown[gene][significance] = count
        significances.add(significance)

    #sort alphabetically to be consistent if there are two or more unranked significance terms
    significances = sorted(significances)

    #sort by rank
    significances = sorted(significances, key=significance_rank)

    return breakdown, significances

def get_breakdown_by_submitter_and_significance(total_variants_by_submitter,
                                                total_variants_by_submitter_and_significance):
    breakdown = OrderedDict()
    significances = set()

    for row in total_variants_by_submitter:
        submitter_id = row['submitter_id']
        submitter_name = row['submitter_name']
        count = row['count']

        breakdown[submitter_id] = {
            'name': submitter_name,
            'counts': {'total': count}
        }

    for row in total_variants_by_submitter_and_significance:
        submitter_id = row['submitter_id']
        significance = row['significance']
        count = row['count']

        breakdown[submitter_id]['counts'][significance] = count
        significances.add(significance)

    #sort alphabetically to be consistent if there are two or more unranked significance terms
    significances = sorted(significances)

    #sort by rank
    significances = sorted(significances, key=significance_rank)

    return breakdown, significances

def get_conflict_breakdown(total_conflicting_variants_by_significance_and_significance):
    breakdown = {}
    submitter1_significances = set()
    submitter2_significances = set()

    for row in total_conflicting_variants_by_significance_and_significance:
        significance1 = row['significance1']
        significance2 = row['significance2']
        conflict_level = row['conflict_level']
        count = row['count']

        if not significance1 in breakdown:
            breakdown[significance1] = {}
        breakdown[significance1][significance2] = {'level': conflict_level, 'count': count}

        submitter1_significances.add(significance1)
        submitter2_significances.add(significance2)

    #sort alphabetically to be consistent if there are two or more unranked significance terms
    submitter1_significances = sorted(submitter1_significances)
    submitter2_significances = sorted(submitter2_significances)

    #sort by rank
    submitter1_significances = sorted(submitter1_significances, key=significance_rank)
    submitter2_significances = sorted(submitter2_significances, key=significance_rank)

    return breakdown, submitter1_significances, submitter2_significances

def get_conflict_summary_by_condition(total_variants_by_condition, total_potentially_conflicting_variants_by_condition,
                                      total_conflicting_variants_by_condition,
                                      total_conflicting_variants_by_condition_and_conflict_level):
    summary = OrderedDict()

    for row in total_conflicting_variants_by_condition:
        condition_name = row['condition_name']
        count = row['count']
        summary[condition_name] = {
            'db': row['condition_db'],
            'id': row['condition_id'],
            'any_conflict': count,
        }

    for row in total_potentially_conflicting_variants_by_condition:
        condition_name = row['condition_name']
        if condition_name in summary: #some conditions have no conflicts at all
            count = row['count']
            summary[condition_name][0] = count - summary[condition_name]['any_conflict']

    for row in total_variants_by_condition:
        condition_name = row['condition_name']
        if condition_name in summary: #some conditions have no conflicts at all
            count = row['count']
            summary[condition_name][-1] = count - summary[condition_name][0] - summary[condition_name]['any_conflict']

    for row in total_conflicting_variants_by_condition_and_conflict_level:
        condition_name = row['condition_name']
        conflict_level = row['conflict_level']
        count = row['count']
        summary[condition_name][conflict_level] = count

    return summary

def get_conflict_summary_by_gene(total_variants_by_gene, total_potentially_conflicting_variants_by_gene,
                                 total_conflicting_variants_by_gene,
                                 total_conflicting_variants_by_gene_and_conflict_level):
    summary = OrderedDict()

    for row in total_conflicting_variants_by_gene:
        gene = row['gene']
        count = row['count']
        summary[gene] = {'any_conflict': count}

    for row in total_potentially_conflicting_variants_by_gene:
        gene = row['gene']
        if gene in summary: #some genes have no conflicts at all
            count = row['count']
            summary[gene][0] = count - summary[gene]['any_conflict']

    for row in total_variants_by_gene:
        gene = row['gene']
        if gene in summary: #some genes have no conflicts at all
            count = row['count']
            summary[gene][-1] = count - summary[gene][0] - summary[gene]['any_conflict']

    for row in total_conflicting_variants_by_gene_and_conflict_level:
        gene = row['gene']
        conflict_level = row['conflict_level']
        count = row['count']
        summary[gene][conflict_level] = count

    return summary

def get_conflict_summary_by_submitter(total_variants_by_submitter, total_potentially_conflicting_variants_by_submitter,
                                      total_conflicting_variants_by_submitter,
                                      total_conflicting_variants_by_submitter_and_conflict_level):
    summary = OrderedDict()

    for row in total_conflicting_variants_by_submitter:
        submitter_id = row['submitter_id']
        submitter_name = row['submitter_name']
        count = row['count']
        summary[submitter_id] = {'name': submitter_name, 'any_conflict': count}

    for row in total_potentially_conflicting_variants_by_submitter:
        submitter_id = row['submitter_id']
        if submitter_id in summary: #some submitters have no conflicts with anyone
            count = row['count']
            summary[submitter_id][0] = count - summary[submitter_id]['any_conflict']

    for row in total_variants_by_submitter:
        submitter_id = row['submitter_id']
        if submitter_id in summary: #some submitters have no conflicts with anyone
            count = row['count']
            summary[submitter_id][-1] = count - summary[submitter_id][0] - summary[submitter_id]['any_conflict']

    for row in total_conflicting_variants_by_submitter_and_conflict_level:
        submitter_id = row['submitter_id']
        conflict_level = row['conflict_level']
        count = row['count']
        summary[submitter_id][conflict_level] = count

    return summary

def get_conflict_overview(total_conflicting_variants_by_conflict_level):
    overview = {}

    for row in total_conflicting_variants_by_conflict_level:
        conflict_level = row['conflict_level']
        count = row['count']
        overview[conflict_level] = count

    return overview

def get_significance_overview(total_variants_by_significance):
    overview = {
        'pathogenic': 0,
        'likely pathogenic': 0,
        'uncertain significance': 0,
        'likely benign': 0,
        'benign': 0,
    }

    for row in total_variants_by_significance:
        significance = row['significance']
        count = row['count']
        overview[significance] = count

    #sort alphabetically to be consistent if there are two or more unranked significance terms
    overview = OrderedDict(sorted(overview.items(), key=lambda pair: pair[0]))

    #sort by rank
    overview = OrderedDict(sorted(overview.items(), key=lambda pair: significance_rank(pair[0])))

    return overview

def int_arg(name, default = -1):
    arg = request.args.get(name)
    try:
        return int(arg) if arg else default
    except ValueError:
        abort(400)

def list_arg(name):
    return list(request.args.getlist(name)) or None

def significance_rank(significance):
    significance_ranks = [
        'pathogenic',
        'likely pathogenic',
        'uncertain significance',
        'likely benign',
        'benign',
        'other',
        'not provided',
    ]
    try:
        rank = significance_ranks.index(nonstandard_significance_term_map.get(significance, significance))
    except ValueError:
        rank = len(significance_ranks) - 2.5 #insert after everything but "other" and "not provided"
    return rank

@app.template_filter('conflictlevel')
def conflict_level_string(conflict_level):
    return [
        'no conflict',
        'synonymous conflict',
        'confidence conflict',
        'benign vs uncertain conflict',
        'category conflict',
        'clinically significant conflict',
    ][conflict_level]

@app.template_filter('extrabreaks')
def extra_breaks(text):
    #provide additional line breaking opportunities
    ret = (text
        .replace('(', '<wbr/>(')
        .replace(')', ')<wbr/>')
        .replace(',', ',<wbr/>')
        .replace('.', '.<wbr/>')
        .replace(':', '<wbr/>:<wbr/>')
        .replace('-', '-<wbr/>')
    )
    ret = re.sub(r'([a-z])([A-Z])', r'\1<wbr/>\2', ret) #camelCase
    return ret

@app.template_filter('genelink')
def gene_link(gene):
    return '<a class="external" href="https://ghr.nlm.nih.gov/gene/' + gene + '">' + gene + '</a>' if gene else ''

@app.template_filter('rcvlink')
def rcv_link(rcv):
    return '<a class="external" href="https://www.ncbi.nlm.nih.gov/clinvar/' + rcv + '/">' + rcv + '</a>'

@app.template_filter('superescaped')
def super_escape(path):
    return SuperEscapedConverter.to_url(path)

@app.context_processor
def template_functions():
    def condition_link(condition_db, condition_id, condition_name):
        #find and order DB names and examples with:
        #SELECT condition_db, condition_id, COUNT(*) FROM current_submissions GROUP BY condition_db ORDER BY COUNT(*) DESC
        condition_db = condition_db.lower()
        if condition_db in ('medgen', 'umls'):
            url = 'https://www.ncbi.nlm.nih.gov/medgen/' + condition_id
        elif condition_db == 'omim':
            url = 'https://www.omim.org/entry/' + condition_id
        elif condition_db == 'genereviews':
            url = 'https://www.ncbi.nlm.nih.gov/books/' + condition_id
        elif condition_db == 'hp':
            url = 'http://compbio.charite.de/hpoweb/showterm?id=' + condition_id
        elif condition_db == 'mesh':
            url = 'https://www.ncbi.nlm.nih.gov/mesh/?term=' + condition_id
        elif condition_db == 'omim phenotypic series':
            url = 'https://www.omim.org/phenotypicseries/' + condition_id
        else:
            url = None

        if url:
            return '<a class="external" href="' + url + '">' + condition_name + '</a>'
        else:
            return condition_name

    def h2(text):
        section_id = text.lower().replace(' ', '-')
        return '<h2 id="' + section_id + '">' + text + ' <a class="internal" href="' + request.url + '#' + section_id + '">#</a></h2>'

    def submitter_link(submitter_id, submitter_name):
        if submitter_id == 0:
            return submitter_name
        return '<a class="external" href="https://www.ncbi.nlm.nih.gov/clinvar/submitters/' + str(submitter_id) + '/">' + extra_breaks(submitter_name) + '</a>'

    def submitter_tagline(submitter_info, submitter_primary_method):
        tagline = '<div class="tagline">'
        if 'country_name' in submitter_info:
            tagline += 'Location: ' + submitter_info['country_name'] + ' &mdash; '
        tagline += 'Primary collection method: ' + submitter_primary_method
        tagline += '</div>'
        return tagline

    def query_suffix(*extra_allowed_params):
        if not request.args:
            return ''

        always_allowed_params = [
            'min_stars1',
            'min_stars2',
            'method1',
            'method2'
        ]

        args = []
        for key in request.args:
            value = request.args.get(key)
            if (key in always_allowed_params or key in extra_allowed_params) and value:
                args.append(quote(key, safe='') + '=' + quote(request.args[key], safe=''))
        return '?' + '&'.join(args) if args else ''

    def variant_link(variant_id, variant_name, variant_rsid):
        if variant_id == 0:
            return variant_name

        ret = '<a class="external" href="https://www.ncbi.nlm.nih.gov/clinvar/variation/' + str(variant_id) + '/">' + extra_breaks(variant_name) + '</a>'

        if variant_rsid:
            ret += ' (<a class="external" href="https://www.ncbi.nlm.nih.gov/SNP/snp_ref.cgi?rs=' + variant_rsid + '">' + variant_rsid + '</a>)'

        return ret

    return {
        'h2': h2,
        'int_arg': int_arg,
        'submitter_link': submitter_link,
        'submitter_tagline': submitter_tagline,
        'condition_link': condition_link,
        'query_suffix': query_suffix,
        'variant_link': variant_link,
    }

@app.before_request
def cache_get():
    response = cache.get(request.url)
    if not response or 'gzip' not in request.accept_encodings:
        return None

    server_etag = response.get_etag()[0]
    client_etags = request.if_none_match
    if server_etag in client_etags:
        return Response(status=304, headers={'ETag': server_etag})

    return response

@app.after_request
def cache_set(response):
    if (ttl >= 0 and not cache.has(request.url) and response.status_code == 200 and not response.direct_passthrough and
            'gzip' in request.accept_encodings):
        response.set_data(gzip.compress(response.get_data()))
        response.set_etag(sha256(response.get_data()).hexdigest())
        response.headers.set('Content-Encoding', 'gzip')
        response.freeze()
        cache.set(request.url, response, timeout=ttl)
    return response

@app.route('/conflicting-variants-by-condition')
def conflicting_variants_by_condition():
    db = DB()

    return render_template(
        'conflicting-variants-by-condition.html',
        overview=get_conflict_overview(
            db.total_conflicting_variants_by_conflict_level(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=int_arg('min_conflict_level'),
                condition1_name=list_arg('conditions'),
            ),
        ),
        total_variants=db.total_variants(
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            condition1_name=list_arg('conditions'),
        ),
        total_potentially_conflicting_variants=db.total_variants(
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            min_conflict_level=0,
            condition1_name=list_arg('conditions'),
        ),
        total_conflicting_variants=db.total_variants(
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            min_conflict_level=max(1, int_arg('min_conflict_level')),
            condition1_name=list_arg('conditions'),
        ),
        summary=get_conflict_summary_by_condition(
            db.total_variants_by_condition(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                condition_names=list_arg('conditions'),
            ),
            db.total_variants_by_condition(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=0,
                condition_names=list_arg('conditions'),
            ),
            db.total_variants_by_condition(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
                condition_names=list_arg('conditions'),
            ),
            db.total_conflicting_variants_by_condition_and_conflict_level(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                condition_names=list_arg('conditions'),
            ),
        ),
    )

@app.route('/conflicting-variants-by-gene')
@app.route('/conflicting-variants-by-gene/<superescaped:gene>')
@app.route('/conflicting-variants-by-gene/<superescaped:gene>/<superescaped:significance1>/<superescaped:significance2>')
def conflicting_variants_by_gene(gene = None, significance1 = None, significance2 = None):
    db = DB()

    if not gene:
        return render_template(
            'conflicting-variants-by-gene.html',
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    gene=list_arg('genes'),
                ),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                gene=list_arg('genes'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=0,
                gene=list_arg('genes'),
            ),
            total_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
                gene=list_arg('genes'),
            ),
            summary=get_conflict_summary_by_gene(
                db.total_variants_by_gene(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    genes=list_arg('genes'),
                ),
                db.total_variants_by_gene(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=0,
                    genes=list_arg('genes'),
                ),
                db.total_variants_by_gene(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=max(1, int_arg('min_conflict_level')),
                    genes=list_arg('genes'),
                ),
                db.total_conflicting_variants_by_gene_and_conflict_level(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    genes=list_arg('genes'),
                ),
            ),
        )

    if gene == 'intergenic':
        gene = ''
    elif not db.is_gene(gene):
        abort(404)

    if not significance1:
        breakdown, submitter1_significances, submitter2_significances = get_conflict_breakdown(
            db.total_conflicting_variants_by_significance_and_significance(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'conflicting-variants-by-gene--gene.html',
            gene=gene,
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    gene=gene,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                ),
            ),
            total_variants=db.total_variants(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                min_conflict_level=0,
                standardized_method2=request.args.get('method2'),
            ),
            variants=db.variants(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
            ),
            breakdown=breakdown,
            submitter1_significances=submitter1_significances,
            submitter2_significances=submitter2_significances,
        )

    if not db.is_significance(significance1) or not db.is_significance(significance2):
        abort(404)

    return render_template(
        'conflicting-variants-by-gene--2significances.html',
        gene=gene,
        significance1=significance1,
        significance2=significance2,
        variants=db.variants(
            gene=gene,
            significance1=significance1,
            significance2=significance2,
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            original_terms=request.args.get('original_terms'),
        ),
    )

@app.route('/conflicting-variants-by-significance')
@app.route('/conflicting-variants-by-significance/<superescaped:significance1>/<superescaped:significance2>')
def conflicting_variants_by_significance(significance1 = None, significance2 = None):
    db = DB()

    if not significance2:
        breakdown, submitter1_significances, submitter2_significances = get_conflict_breakdown(
            db.total_conflicting_variants_by_significance_and_significance(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                original_terms=request.args.get('original_terms'),
                min_conflict_level=int_arg('min_conflict_level'),
            )
        )

        return render_template(
            'conflicting-variants-by-significance.html',
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                ),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=0,
            ),
            total_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
            ),
            breakdown=breakdown,
            submitter1_significances=submitter1_significances,
            submitter2_significances=submitter2_significances,
        )

    if not db.is_significance(significance1) or not db.is_significance(significance2):
        abort(404)

    return render_template(
        'conflicting-variants-by-significance--2significances.html',
        significance1=significance1,
        significance2=significance2,
        variants=db.variants(
            significance1=significance1,
            significance2=significance2,
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            original_terms=request.args.get('original_terms'),
        ),
    )

@app.route('/conflicting-variants-by-submitter')
@app.route('/conflicting-variants-by-submitter/<int:submitter1_id>')
@app.route('/conflicting-variants-by-submitter/<int:submitter1_id>/<int:submitter2_id>')
@app.route('/conflicting-variants-by-submitter/<int:submitter1_id>/<int:submitter2_id>/<superescaped:significance1>/<superescaped:significance2>')
def conflicting_variants_by_submitter(submitter1_id = None, submitter2_id = None, significance1 = None, significance2 = None):
    db = DB()

    if submitter1_id == None:
        return render_template(
            'conflicting-variants-by-submitter.html',
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    submitter1_id=list_arg('submitters'),
                ),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                submitter1_id=list_arg('submitters'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=0,
                submitter1_id=list_arg('submitters'),
            ),
            total_conflicting_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
                submitter1_id=list_arg('submitters'),
            ),
            summary=get_conflict_summary_by_submitter(
                db.total_variants_by_submitter(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    submitter_ids=list_arg('submitters'),
                ),
                db.total_variants_by_submitter(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=0,
                    submitter_ids=list_arg('submitters'),
                ),
                db.total_variants_by_submitter(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=max(1, int_arg('min_conflict_level')),
                    submitter_ids=list_arg('submitters'),
                ),
                db.total_conflicting_variants_by_submitter_and_conflict_level(
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    submitter_ids=list_arg('submitters'),
                ),
            ),
        )

    submitter1_info = db.submitter_info(submitter1_id)
    if not submitter1_info:
        abort(404)

    if submitter2_id == None:
        breakdown, submitter1_significances, submitter2_significances = get_conflict_breakdown(
            db.total_conflicting_variants_by_significance_and_significance(
                submitter1_id=submitter1_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'conflicting-variants-by-submitter--1submitter.html',
            submitter1_info=submitter1_info,
            submitter1_primary_method=db.submitter_primary_method(submitter1_id),
            submitter2_info={'id': 0, 'name': 'All submitters'},
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    submitter1_id=submitter1_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                ),
            ),
            total_variants=db.total_variants(
                submitter1_id=submitter1_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                submitter1_id=submitter1_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=0,
            ),
            total_conflicting_variants=db.total_variants(
                submitter1_id=submitter1_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
            ),
            summary=get_conflict_summary_by_submitter(
                db.total_variants_by_submitter(
                    submitter1_id=submitter1_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                ),
                db.total_variants_by_submitter(
                    submitter1_id=submitter1_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=0,
                ),
                db.total_variants_by_submitter(
                    submitter1_id=submitter1_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=max(1, int_arg('min_conflict_level')),
                ),
                db.total_conflicting_variants_by_submitter_and_conflict_level(
                    submitter1_id=submitter1_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                ),
            ),
            breakdown=breakdown,
            submitter1_significances=submitter1_significances,
            submitter2_significances=submitter2_significances,
            variants=db.variants(
                submitter1_id=submitter1_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
            ),
        )

    if submitter2_id == 0:
        submitter2_info = {'id': 0, 'name': 'any other submitter'}
    else:
        submitter2_info = db.submitter_info(submitter2_id)
        if not submitter2_info:
            abort(404)

    if not significance1:
        breakdown, submitter1_significances, submitter2_significances = get_conflict_breakdown(
            db.total_conflicting_variants_by_significance_and_significance(
                submitter1_id=submitter1_id,
                submitter2_id=submitter2_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'conflicting-variants-by-submitter--2submitters.html',
            submitter1_info=submitter1_info,
            submitter2_info=submitter2_info,
            overview=get_conflict_overview(
                db.total_conflicting_variants_by_conflict_level(
                    submitter1_id=submitter1_id,
                    submitter2_id=submitter2_id,
                    min_stars1=int_arg('min_stars1'),
                    standardized_method1=request.args.get('method1'),
                    min_stars2=int_arg('min_stars2'),
                    standardized_method2=request.args.get('method2'),
                    min_conflict_level=int_arg('min_conflict_level'),
                ),
            ),
            total_variants=db.total_variants(
                submitter1_id=submitter1_id,
                submitter2_id=submitter2_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
            ),
            total_potentially_conflicting_variants=db.total_variants(
                submitter1_id=submitter1_id,
                submitter2_id=submitter2_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=0,
            ),
            submitter1_significances=submitter1_significances,
            submitter2_significances=submitter2_significances,
            breakdown=breakdown,
            variants=db.variants(
                submitter1_id=submitter1_id,
                submitter2_id=submitter2_id,
                min_stars1=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                min_stars2=int_arg('min_stars2'),
                standardized_method2=request.args.get('method2'),
                min_conflict_level=max(1, int_arg('min_conflict_level')),
            ),
        )

    if not db.is_significance(significance1) or not db.is_significance(significance2):
        abort(404)

    return render_template(
        'conflicting-variants-by-submitter--2significances.html',
        submitter1_info=submitter1_info,
        submitter2_info=submitter2_info,
        significance1=significance1,
        significance2=significance2,
        variants=db.variants(
            submitter1_id=submitter1_id,
            submitter2_id=submitter2_id,
            significance1=significance1,
            significance2=significance2,
            min_stars1=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            min_stars2=int_arg('min_stars2'),
            standardized_method2=request.args.get('method2'),
            original_terms=request.args.get('original_terms'),
        ),
    )

@app.route('/')
def index():
    db = DB()
    return render_template(
        'index.html',
        max_date=datetime.strptime(db.max_date(), '%Y-%m'),
        total_submissions=db.total_submissions(),
        total_variants=db.total_variants(),
    )

@app.route('/robots.txt')
def robots_txt():
    return app.send_static_file('robots.txt')

@app.route('/search')
def search():
    db = DB()
    query = request.args.get('q')

    #blank
    if not query:
        return redirect(request.url_root)

    #rsID, RCV, SCV
    variant_name = (
        db.variant_name_from_rsid(query.lower()) or
        db.variant_name_from_rcv(query.upper()) or
        db.variant_name_from_scv(query.upper())
    )
    if variant_name:
        return redirect(request.script_root + '/submissions-by-variant/' + super_escape(variant_name))

    #an rsID always uniquely identifies a gene even if it doesn't uniquely identify a variant
    gene = db.gene_from_rsid(query.lower())
    if gene != None:
        if gene == '':
            return redirect(request.script_root + '/variants-by-gene/intergenic')
        else:
            return redirect(request.script_root + '/variants-by-gene/' + super_escape(gene))

    #gene
    if db.is_gene(query.upper()):
        return redirect(request.script_root + '/variants-by-gene/' + super_escape(query.upper()))
    if query.lower() == 'intergenic':
        return redirect(request.script_root + '/variants-by-gene/intergenic')

    #HGVS
    if db.is_variant_name(query):
        return redirect(request.script_root + '/submissions-by-variant/' + super_escape(query))

    #condition
    if db.is_condition_name(query):
        return redirect(request.script_root + '/variants-by-condition/' + super_escape(query))

    #submitter
    submitter_id = db.submitter_id_from_name(query)
    if submitter_id:
        return redirect(request.script_root + '/variants-by-submitter/' + str(submitter_id))

    keywords = urlparse(request.url_root)[1] + ' ' + query
    return redirect('https://www.google.com/search?q=site:' + quote(keywords, safe=''))

@app.route('/significance-terms')
def significance_terms():
    db = DB()

    return render_template(
        'significance-terms.html',
        total_significance_terms_over_time=db.total_significance_terms_over_time(),
        significance_term_info=db.significance_term_info(),
        max_date=db.max_date(),
    )

@app.route('/submissions-by-variant/<superescaped:variant_name>')
def submissions_by_variant(variant_name):
    db = DB()

    variant_info = db.variant_info(variant_name)
    if not variant_info:
        abort(404)

    return render_template(
        'submissions-by-variant--variant.html',
        variant_info=db.variant_info(variant_name),
        submissions=db.submissions(
            variant_name=variant_name,
            min_stars=int_arg('min_stars1'),
            standardized_method=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
        ),
    )

@app.route('/total-submissions-by-country')
@app.route('/total-submissions-by-country/', defaults={'country_code': ''})
@app.route('/total-submissions-by-country/<country_code>')
def total_submissions_by_country(country_code = None):
    db = DB()

    if country_code == None:
        return render_template(
            'total-submissions-by-country.html',
            total_submissions_by_country=db.total_submissions_by_country(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
            ),
        )

    country_name = db.country_name(country_code)
    if country_name == None:
        abort(404)

    return render_template(
        'total-submissions-by-country--country.html',
        country_name=country_name,
        total_submissions_by_submitter=db.total_submissions_by_submitter(
            country_code=country_code,
            min_stars=int_arg('min_stars1'),
            standardized_method=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
        ),
    )

@app.route('/total-submissions-by-method')
def total_submissions_by_method():
    db = DB()
    rows = db.total_submissions_by_standardized_method_over_time(
        min_stars=int_arg('min_stars1'),
        min_conflict_level=int_arg('min_conflict_level'),
    )

    dates = set()
    methods = set()
    date_method_pairs = set()
    for row in rows:
        date = row['date']
        method = row['standardized_method']
        count = row['count']

        dates.add(date)
        methods.add(method)
        date_method_pairs.add((date, method))

    for date in dates:
        for method in methods:
            if not (date, method) in date_method_pairs:
                rows.append({'date': date, 'standardized_method': method, 'count': 0})

    rows.sort(key=lambda row: row['date'])

    return render_template(
        'total-submissions-by-method.html',
        total_submissions_by_standardized_method_over_time=rows,
        total_submissions_by_method=db.total_submissions_by_method(
            min_stars=int_arg('min_stars1'),
            min_conflict_level=int_arg('min_conflict_level'),
        ),
    )

@app.route('/variants-by-condition')
@app.route('/variants-by-condition/<superescaped:condition_name>')
@app.route('/variants-by-condition/<superescaped:condition_name>/significance/any', defaults={'significance': ''})
@app.route('/variants-by-condition/<superescaped:condition_name>/significance/<superescaped:significance>')
@app.route('/variants-by-condition/<superescaped:condition_name>/gene/<superescaped:gene>', defaults={'significance': ''})
@app.route('/variants-by-condition/<superescaped:condition_name>/gene/<superescaped:gene>/<superescaped:significance>')
@app.route('/variants-by-condition/<superescaped:condition_name>/submitter/<int:submitter_id>', defaults={'significance': ''})
@app.route('/variants-by-condition/<superescaped:condition_name>/submitter/<int:submitter_id>/<superescaped:significance>')
def variants_by_condition(significance = None, condition_name = None, gene = None, submitter_id = None):
    db = DB()

    if condition_name == None:
        return render_template(
            'variants-by-condition.html',
            total_variants_by_condition=db.total_variants_by_condition(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                condition_names=list_arg('conditions'),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                condition1_name=list_arg('conditions'),
            ),
        )

    condition_info = db.condition_info(condition_name)
    if not condition_info:
        abort(404)

    if significance == None and gene == None and submitter_id == None:
        breakdown_by_gene_and_significance, significances = get_breakdown_by_gene_and_significance(
            db.total_variants_by_gene(
                condition1_name=condition_name,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_gene_and_significance(
                condition_name=condition_name,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        breakdown_by_submitter_and_significance, significances = get_breakdown_by_submitter_and_significance(
            db.total_variants_by_submitter(
                condition1_name=condition_name,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_submitter_and_significance(
                condition_name=condition_name,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'variants-by-condition--condition.html',
            condition_info=condition_info,
            overview=get_significance_overview(
                db.total_variants_by_significance(
                    condition_name=condition_name,
                    min_stars=int_arg('min_stars1'),
                    standardized_method=request.args.get('method1'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    original_terms=request.args.get('original_terms'),
                )
            ),
            significances=significances,
            breakdown_by_gene_and_significance=breakdown_by_gene_and_significance,
            breakdown_by_submitter_and_significance=breakdown_by_submitter_and_significance,
            total_variants=db.total_variants(
                condition1_name=condition_name,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
            ),
        )

    if significance and not db.is_significance(significance):
        abort(404)

    if gene == None and submitter_id == None:
        return render_template(
            'variants-by-condition--condition-significance.html',
            condition_info=condition_info,
            significance=significance,
            variants=db.variants(
                condition1_name=condition_name,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if gene:
        if gene == 'intergenic':
            gene = ''
        elif not db.is_gene(gene):
            abort(404)

        return render_template(
            'variants-by-condition--condition-gene-significance.html',
            condition_info=condition_info,
            gene=gene,
            significance=significance,
            variants=db.variants(
                gene=gene,
                condition1_name=condition_name,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if submitter_id:
        submitter_info = db.submitter_info(submitter_id)
        if not submitter_info:
            abort(404)

        return render_template(
            'variants-by-condition--condition-submitter-significance.html',
            condition_info=condition_info,
            submitter_info=submitter_info,
            significance=significance,
            variants=db.variants(
                condition1_name=condition_name,
                submitter1_id=submitter_id,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

@app.route('/variants-by-gene')
@app.route('/variants-by-gene/<superescaped:gene>')
@app.route('/variants-by-gene/<superescaped:gene>/significance/any', defaults={'significance': ''})
@app.route('/variants-by-gene/<superescaped:gene>/significance/<superescaped:significance>')
@app.route('/variants-by-gene/<superescaped:gene>/submitter/<int:submitter_id>', defaults={'significance': ''})
@app.route('/variants-by-gene/<superescaped:gene>/submitter/<int:submitter_id>/<superescaped:significance>')
@app.route('/variants-by-gene/<superescaped:gene>/condition/<superescaped:condition_name>', defaults={'significance': ''})
@app.route('/variants-by-gene/<superescaped:gene>/condition/<superescaped:condition_name>/<superescaped:significance>')
def variants_by_gene(gene = None, significance = None, submitter_id = None, condition_name = None):
    db = DB()

    if gene == None:
        return render_template(
            'variants-by-gene.html',
            total_variants_by_gene=db.total_variants_by_gene(
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                genes=list_arg('genes'),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                gene=list_arg('genes'),
            ),
        )

    if gene == 'intergenic':
        gene = ''
    elif not db.is_gene(gene):
        abort(404)

    if significance == None and submitter_id == None and condition_name == None:
        breakdown_by_condition_and_significance, significances = get_breakdown_by_condition_and_significance(
            db.total_variants_by_condition(
                gene=gene,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_condition_and_significance(
                gene=gene,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        breakdown_by_submitter_and_significance, significances = get_breakdown_by_submitter_and_significance(
            db.total_variants_by_submitter(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_submitter_and_significance(
                gene=gene,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'variants-by-gene--gene.html',
            gene=gene,
            overview=get_significance_overview(
                db.total_variants_by_significance(
                    gene=gene,
                    min_stars=int_arg('min_stars1'),
                    standardized_method=request.args.get('method1'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    original_terms=request.args.get('original_terms'),
                )
            ),
            significances=significances,
            breakdown_by_condition_and_significance=breakdown_by_condition_and_significance,
            breakdown_by_submitter_and_significance=breakdown_by_submitter_and_significance,
            total_variants=db.total_variants(
                gene=gene,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
            ),
        )

    if significance and not db.is_significance(significance):
        abort(404)

    if submitter_id == None and condition_name == None:
        return render_template(
            'variants-by-gene--gene-significance.html',
            gene=gene,
            significance=significance,
            variants=db.variants(
                gene=gene,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if submitter_id:
        submitter_info = db.submitter_info(submitter_id)
        if not submitter_info:
            abort(404)

        return render_template(
            'variants-by-gene--gene-submitter-significance.html',
            gene=gene,
            submitter_info=submitter_info,
            significance=significance,
            variants=db.variants(
                gene=gene,
                submitter1_id=submitter_id,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if condition_name:
        condition_info = db.condition_info(condition_name)
        if not condition_info:
            abort(404)

        return render_template(
            'variants-by-gene--gene-condition-significance.html',
            gene=gene,
            condition_info=condition_info,
            significance=significance,
            variants=db.variants(
                gene=gene,
                condition1_name=condition_name,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

@app.route('/variants-by-significance')
@app.route('/variants-by-significance/<superescaped:significance>')
def variants_by_significance(significance = None):
    db = DB()

    if significance == None:
        return render_template(
            'variants-by-significance.html',
            total_variants_by_significance=db.total_variants_by_significance(
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if not db.is_significance(significance):
        abort(404)

    return render_template(
        'variants-by-significance--significance.html',
        significance=significance,
        total_variants=db.total_variants(
            significance1=significance,
            min_stars1=int_arg('min_stars1'),
            min_stars2=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            standardized_method2=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
            original_terms=request.args.get('original_terms'),
        ),
        total_variants_by_submitter=db.total_variants_by_submitter(
            significance1=significance,
            min_stars1=int_arg('min_stars1'),
            min_stars2=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            standardized_method2=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
            original_terms=request.args.get('original_terms'),
        ),
        total_variants_by_gene=db.total_variants_by_gene(
            significance1=significance,
            min_stars1=int_arg('min_stars1'),
            min_stars2=int_arg('min_stars1'),
            standardized_method1=request.args.get('method1'),
            standardized_method2=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
            original_terms=request.args.get('original_terms'),
        ),
        total_variants_by_condition=db.total_variants_by_condition(
            significance1=significance,
            min_stars=int_arg('min_stars1'),
            standardized_method=request.args.get('method1'),
            min_conflict_level=int_arg('min_conflict_level'),
            original_terms=request.args.get('original_terms'),
        ),
    )

@app.route('/variants-by-submitter')
@app.route('/variants-by-submitter/<int:submitter_id>')
@app.route('/variants-by-submitter/<int:submitter_id>/significance/any', defaults={'significance': ''})
@app.route('/variants-by-submitter/<int:submitter_id>/significance/<superescaped:significance>')
@app.route('/variants-by-submitter/<int:submitter_id>/gene/<superescaped:gene>', defaults={'significance': ''})
@app.route('/variants-by-submitter/<int:submitter_id>/gene/<superescaped:gene>/<superescaped:significance>')
@app.route('/variants-by-submitter/<int:submitter_id>/condition/<superescaped:condition_name>', defaults={'significance': ''})
@app.route('/variants-by-submitter/<int:submitter_id>/condition/<superescaped:condition_name>/<superescaped:significance>')
def variants_by_submitter(submitter_id = None, significance = None, gene = None, condition_name = None):
    db = DB()

    if submitter_id == None:
        return render_template(
            'variants-by-submitter.html',
            total_variants_by_submitter=db.total_variants_by_submitter(
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                submitter_ids=list_arg('submitters'),
            ),
            total_variants=db.total_variants(
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                submitter1_id=list_arg('submitters'),
            ),
        )

    submitter_info = db.submitter_info(submitter_id)
    if not submitter_info:
        abort(404)

    if significance == None and gene == None and condition_name == None:
        breakdown_by_gene_and_significance, significances = get_breakdown_by_gene_and_significance(
            db.total_variants_by_gene(
                submitter1_id=submitter_id,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_gene_and_significance(
                submitter_id=submitter_id,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        breakdown_by_condition_and_significance, significances = get_breakdown_by_condition_and_significance(
            db.total_variants_by_condition(
                submitter1_id=submitter_id,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
            db.total_variants_by_condition_and_significance(
                submitter_id=submitter_id,
                min_stars=int_arg('min_stars1'),
                standardized_method=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            )
        )

        return render_template(
            'variants-by-submitter--submitter.html',
            submitter_info=submitter_info,
            submitter_primary_method=db.submitter_primary_method(submitter_id),
            overview=get_significance_overview(
                db.total_variants_by_significance(
                    submitter_id=submitter_id,
                    min_stars=int_arg('min_stars1'),
                    standardized_method=request.args.get('method1'),
                    min_conflict_level=int_arg('min_conflict_level'),
                    original_terms=request.args.get('original_terms'),
                )
            ),
            significances=significances,
            breakdown_by_gene_and_significance=breakdown_by_gene_and_significance,
            breakdown_by_condition_and_significance=breakdown_by_condition_and_significance,
            total_variants=db.total_variants(
                submitter1_id=submitter_id,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
            ),
        )

    if significance and not db.is_significance(significance):
        abort(404)

    if gene == None and condition_name == None:
        return render_template(
            'variants-by-submitter--submitter-significance.html',
            submitter_info=submitter_info,
            significance=significance,
            variants=db.variants(
                submitter1_id=submitter_id,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if gene:
        if gene == 'intergenic':
            gene = ''
        elif not db.is_gene(gene):
            abort(404)

        return render_template(
            'variants-by-submitter--submitter-gene-significance.html',
            gene=gene,
            submitter_info=submitter_info,
            significance=significance,
            variants=db.variants(
                gene=gene,
                submitter1_id=submitter_id,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )

    if condition_name:
        condition_info = db.condition_info(condition_name)
        if not condition_info:
            abort(404)

        return render_template(
            'variants-by-submitter--submitter-condition-significance.html',
            condition_info=condition_info,
            submitter_info=submitter_info,
            significance=significance,
            variants=db.variants(
                condition1_name=condition_name,
                submitter1_id=submitter_id,
                significance1=significance,
                min_stars1=int_arg('min_stars1'),
                min_stars2=int_arg('min_stars1'),
                standardized_method1=request.args.get('method1'),
                standardized_method2=request.args.get('method1'),
                min_conflict_level=int_arg('min_conflict_level'),
                original_terms=request.args.get('original_terms'),
            ),
        )
