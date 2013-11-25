import ast
import datetime
import random
import re
import string

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.urlresolvers import reverse
from django.db import connection
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render_to_response
from django.template.context import RequestContext
from django.views.decorators.http import require_http_methods

from django_facebook.decorators import facebook_required_lazy
from open_facebook import exceptions as FacebookException
from open_facebook.api import *

from project.settings import FACEBOOK_APP_ID

from referendum import tasks
from referendum.models import Vote, ActiveVote, FacebookUserWithLocation
from referendum.utils import get_percentages

def example(request):
    #TODO: Jako glupo ali neka zasad bude ovako.
    #TODO: Koristiti funkcije results i friends_results s xhr
    #TODO: Staviti cache za friends_results
    context = RequestContext(request)
    if request.user.is_authenticated():
        #TODO: makni filter
        votes = Vote.objects.filter(facebook_id=request.user.facebook_id).order_by('-date')
        if len(votes) >= 1:
            vote = votes[0]
        else:
            vote = None

        key = 'friends_{}'.format(request.user.id)
        result = cache.get(key)
        if result is None:
            cursor = connection.cursor()
            cursor.execute(
                'SELECT vote, COUNT(vote) ' +
                'FROM django_facebook_facebookuser AS fb ' +
                'JOIN referendum_activevote AS v ' +
                'ON fb.facebook_id = v.facebook_id ' +
                'WHERE fb.user_id=%s ' +
                'GROUP BY vote', [request.user.id]
                )
            result = '{}'.format(cursor.fetchall())
            cache.set(key, result)

        #TODO: Ovo je lose!
        #TODO: Nepotrebna konverzija: arr of tuples --> string --> row_as_dict
        #TODO: Potrebno je postaviti row_as_dict u cache, a ne pretacunavati
        friends_rows = list(ast.literal_eval(result))
        friends_results= []

        for row in friends_rows:
            row_as_dict = {
                'vote' : row[0],
                'vote_count' : str(row[1])}
            friends_results.append(row_as_dict)

    else:
        vote = None
        #TODO: set null
        friends_results = -1


    if vote is None:
	vote_value = -1
    else:
        vote_value = vote.vote

    key = 'global_results'
    global_results = cache.get(key)
    if global_results is None:
        global_results = '{}'.format(ActiveVote.objects.values('vote').annotate(vote_count=Count('vote')))
        cache.set(key, global_results)
    context['vote'] = vote_value
    context['global_results'] = global_results
    context['friends_results'] = friends_results
    return render_to_response('referendum/main.html', context)

def results(request):
    #TODO: vrati JSON
    #TODO: napravi ovo bolje
    if not request.user.is_authenticated():
        raise PermissionDenied
    key = 'global_results'
    result = cache.get(key)
    if result is None:
        result = '{}'.format(ActiveVote.objects.values('vote').annotate(Count('vote')))
        cache.set(key, result)

    return HttpResponse(result)

def friends_results(request):
    #TODO: vrati JSON
    #TODO: napravi ovo bolje
    if not request.user.is_authenticated():
        raise PermissionDenied
    key = 'friends_{}'.format(request.user.id)
    result = cache.get(key)
    if result is None:
        cursor = connection.cursor()
        cursor.execute(
            'SELECT vote, COUNT(vote) ' +
                'FROM django_facebook_facebookuser AS fb ' +
                'JOIN referendum_activevote AS v ' +
                    'ON fb.facebook_id = v.facebook_id ' +
                'WHERE fb.user_id=%s ' +
                'GROUP BY vote',
            [request.user.id]
        )
        result = '{}'.format(cursor.fetchall())
        cache.set(key, result)
    return HttpResponse(result)

@login_required
def vote2(request):
    try:
        vote = int(request.POST['choice'])
    except (KeyError, ValueError) as e:
        return HttpResponseBadRequest('ERROR 400: Zlocko! Note to self: Napisi ovo do kraja.')
    tasks.save_vote.delay(request.user.facebook_id, vote)
    return HttpResponseRedirect(reverse('referendum:example'))

@login_required
@require_http_methods(["POST"])
def vote(request):

    if not request.user.is_authenticated():
        raise PermissionDenied

    vote = -1

    try:
        vote = int(request.POST['vote'])
        if vote < 0 or vote > 1 :
            return HttpResponseBadRequest('ERROR 400')
        tasks.save_vote.delay(request.user.facebook_id, vote)
    except (KeyError, ValueError) as e:
        return HttpResponseBadRequest('ERROR 400')

    #cache.set(key, result)
    return HttpResponse(vote)

@facebook_required_lazy(scope=['publish_actions'])
def post_og_actions(request):
    if not request.user.is_authenticated():
        raise PermissionDenied

    vote = get_object_or_404(ActiveVote, facebook_id=request.user.facebook_id)
    (friends_percentages, global_percentages) = get_percentages(request.user.id)

    data = {}
    data['app_id'] = FACEBOOK_APP_ID
    data['url'] = 'http://referendum2013.hr/'
    #TODO: FB pokupi title sa URL-a. To ne zelimo, pa trebamo nekako rijesiti.
    data['title'] = 'Za' if vote.vote == 1 else 'Protiv'
    data['image'] = 'http://referendum2013.hr/static/images/logo.png'
    data['type'] = 'referendum_hr:vote'

    #TODO: Sto ako nema dovoljno glasova? -> nadji bolje tekstove
    data['description'] = '''{} {} je glasa{} {}.
Ukupno je {}% ZA, a {}% PROTIV.
Sudjeluj i ti!'''.format(
        request.user.first_name,
        request.user.last_name,
        'o' if request.user.gender == 'm' else 'la' ,
        'ZA' if vote.vote == 1 else 'PROTIV',
        global_percentages[1],
        global_percentages[0],
    )

    print data
    print request.user.access_token
    #facebook = OpenFacebook(request.user.access_token)
    #facebook.set(
    #    '/me/objects/referendum_hr:vote',
    #    object=data
    #)

    return HttpResponse(data)
    return HttpResponseRedirect(reverse('referendum:example'))

