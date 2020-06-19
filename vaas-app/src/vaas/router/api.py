# -*- coding: utf-8 -*-
from tastypie.resources import ModelResource, ALL_WITH_RELATIONS, Resource
from tastypie import fields
from tastypie.authentication import ApiKeyAuthentication, MultiAuthentication, SessionAuthentication
from tastypie.exceptions import ImmediateHttpResponse
from celery.result import AsyncResult
from django.http.response import HttpResponse
from django.conf.urls import url
from django.core.exceptions import ObjectDoesNotExist
from vaas.external.tasty_validation import ModelCleanedDataFormValidation

from vaas.external.api import ExtendedDjangoAuthorization as DjangoAuthorization
from vaas.external.serializer import PrettyJSONSerializer
from vaas.router.models import Route, PositiveUrl, RoutesTestTask, provide_route_configuration
from vaas.router.forms import RouteModelForm
from vaas.router.test import make_routes_test
from vaas.adminext.widgets import split_complex_condition, split_condition
from vaas.external.oauth import VaasMultiAuthentication


class PositiveUrlResource(Resource):
    url = fields.CharField(attribute='url')

    def dehydrate(self, bundle):
        bundle = super().dehydrate(bundle)
        del bundle.data['resource_uri']
        return bundle


class RoutesTestRequest(Resource):

    class Meta:
        resource_name = 'routes_test_request'
        list_allowed_methods = ['post']
        authorization = DjangoAuthorization()
        authentication = VaasMultiAuthentication(ApiKeyAuthentication())
        include_resource_uri = False

    def obj_create(self, bundle, **kwargs):
        task = make_routes_test.delay()
        raise ImmediateHttpResponse(self.create_http_response(task.id))

    def get_object_list(self, request):
        return None

    def create_http_response(self, task_id):
        response = HttpResponse(status=202)
        response.setdefault('Location', '/api/v0.1/routes_test_task/{}/'.format(task_id))
        return response


class RoutesTestTaskResult(Resource):
    status = fields.CharField(attribute='status')
    info = fields.CharField(attribute='info')

    class Meta:
        resource_name = 'routes_test_task'
        list_allowed_methods = ['get']
        authorization = DjangoAuthorization()
        authentication = VaasMultiAuthentication(ApiKeyAuthentication())
        fields = ['status', 'info']
        include_resource_uri = False

    def obj_get(self, bundle, **kwargs):
        task = AsyncResult(kwargs['pk'])
        return RoutesTestTask(kwargs['pk'], task.status, '{}'.format(task.info))

    def get_object_list(self, request):
        return None


class RouteResource(ModelResource):
    director = fields.ForeignKey('vaas.manager.api.DirectorResource', 'director')
    clusters = fields.ToManyField('vaas.cluster.api.LogicalClusterResource', 'clusters')
    positive_urls = fields.ToManyField('vaas.router.api.PositiveUrlResource', 'positive_urls', full=True)

    class Meta:
        queryset = Route.objects.all().prefetch_related('clusters', 'positive_urls')
        resource_name = 'route'
        serializer = PrettyJSONSerializer()
        authorization = DjangoAuthorization()
        authentication = VaasMultiAuthentication(ApiKeyAuthentication())
        validation = ModelCleanedDataFormValidation(form_class=RouteModelForm)
        always_return_data = True
        filtering = {
            'director': ALL_WITH_RELATIONS,
            'clusters': ALL_WITH_RELATIONS,
            'condition': ['icontains']
        }

    def hydrate_condition(self, bundle):
        for i, condition in enumerate(split_complex_condition(bundle.data['condition'])):
            for j, part in enumerate(split_condition(condition)):
                bundle.data['condition_{}_{}'.format(i, j)] = part
        return bundle

    def full_hydrate(self, bundle):
        positive_urls = bundle.data.get('positive_urls', [])
        bundle = super().full_hydrate(bundle)
        bundle.data['positive_urls'] = [p['url'] for p in positive_urls]
        return bundle

    def dehydrate_director(self, bundle):
        return bundle.obj.director.name

    def dehydrate_clusters(self, bundle):
        return list(bundle.obj.clusters.values_list('name', flat=True))

    def save(self, bundle, *args, **kwargs):
        positive_urls = bundle.data.get('positive_urls', [])
        bundle = super().save(bundle, *args, **kwargs)
        bundle.obj.positive_urls.exclude(url__in=positive_urls).delete()
        existing_urls = bundle.obj.positive_urls.values_list('url', flat=True)
        for url in positive_urls:
            if url not in existing_urls:
                PositiveUrl.objects.create(url=url, route=bundle.obj)
        return bundle


class LeftResource(Resource):
    left = fields.CharField(attribute='left')
    name = fields.CharField(attribute='name')

    class Meta:
        include_resource_uri = False


class ActionResource(Resource):
    action = fields.CharField(attribute='action')
    name = fields.CharField(attribute='name')

    class Meta:
        include_resource_uri = False


class OperatorResource(Resource):
    operator = fields.CharField(attribute='operator')
    name = fields.CharField(attribute='name')

    class Meta:
        include_resource_uri = False


class RouteConfigurationResource(Resource):
    lefts = fields.ToManyField(LeftResource, 'lefts', full=True)
    actions = fields.ToManyField(ActionResource, 'actions', full=True)
    operators = fields.ToManyField(OperatorResource, 'operators', full=True)

    class Meta:
        resource_name = 'route_config'
        list_allowed_methods = ['get']
        authorization = DjangoAuthorization()
        authentication = VaasMultiAuthentication(ApiKeyAuthentication())
        fields = ['lefts', 'actions', 'operators']
        include_resource_uri = False

    def prepend_urls(self):
        return [
            url(
                r"^(?P<resource_name>%s)/$" % self._meta.resource_name,
                self.wrap_view('dispatch_detail'),
                name="api_dispatch_detail"
            ),
        ]

    def obj_get(self, bundle, **kwargs):
        if 'pk' in kwargs:
            raise ObjectDoesNotExist()
        return provide_route_configuration()

    def get_object_list(self, request):
        return None


class NamedResource(Resource):
    id = fields.IntegerField(attribute='id')
    name = fields.CharField(attribute='name')

    class Meta:
        include_resource_uri = False


class AssertionResource(Resource):
    route = fields.ToOneField(NamedResource, attribute='route', full=True)
    director = fields.ToOneField(NamedResource, attribute='director', full=True)

    class Meta:
        include_resource_uri = False


class ValidationResultResource(Resource):
    url = fields.CharField(attribute='url')
    result = fields.CharField(attribute='result')
    expected = fields.ToOneField(AssertionResource, attribute='expected', full=True)
    current = fields.ToOneField(AssertionResource, attribute='current', full=True)
    error_message = fields.CharField(attribute='error_message')

    class Meta:
        include_resource_uri = False


class ValidationReportResource(Resource):
    validation_results = fields.ToManyField(ValidationResultResource, 'validation_results', full=True)
    status = fields.CharField(attribute='status')

    class Meta:
        resource_name = 'validation_report'
        list_allowed_methods = ['get']
        authorization = DjangoAuthorization()
        authentication = VaasMultiAuthentication(ApiKeyAuthentication())
        fields = ['validation_results', 'status']
        include_resource_uri = False
