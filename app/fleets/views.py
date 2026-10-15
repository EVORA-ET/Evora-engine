from django.db import transaction
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.api import apply_ordering, get_search, get_user_org, list_response
from fleets.models import Fleet
from fleets.serializers import FleetMembershipSerializer, FleetSerializer
from vehicles.models import Vehicle, VehicleStatus

ORDERING = {'name': 'name', 'created_at': 'created_at'}


def fleet_stats(org, fleet_ids):
    rows = (
        Vehicle.objects.filter(organization=org, fleet_id__in=fleet_ids)
        .values('fleet_id')
        .annotate(
            vehicle_count=Count('id'),
            ev_count=Count('id', filter=Q(fuel_type='ev')),
            active_count=Count('id', filter=Q(status=VehicleStatus.ACTIVE)),
        )
    )
    stats = {}
    for row in rows:
        stats[row['fleet_id']] = {
            'vehicle_count': row['vehicle_count'],
            'ev_count': row['ev_count'],
            'ice_count': row['vehicle_count'] - row['ev_count'],
            'active_count': row['active_count'],
        }
    return stats


def fleet_detail_extras(org, fleet):
    """Depots (derived from the fleet's vehicles) and status breakdown."""
    vehicles = Vehicle.objects.filter(organization=org, fleet=fleet)
    depots = [
        {
            'depot_id': r['depot_id'],
            'depot_name': r['depot__name'],
            'vehicle_count': r['n'],
        }
        for r in vehicles.values('depot_id', 'depot__name')
        .annotate(n=Count('id'))
        .order_by('-n', 'depot__name')
    ]
    status_counts = {
        r['status']: r['n']
        for r in vehicles.values('status').annotate(n=Count('id'))
    }
    return {'depots': depots, 'status_counts': status_counts}


class FleetListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='List fleets',
        description=(
            "Returns fleets for the authenticated user's organization. Without "
            'query parameters a bare array; with `page`/`page_size` a page '
            'envelope. Filters: `search`, `ordering`.'
        ),
        responses={200: FleetSerializer(many=True)},
    )
    def get(self, request):
        org = get_user_org(request.user)
        queryset = Fleet.objects.filter(organization=org)
        search = get_search(request)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        queryset = apply_ordering(queryset, request, ORDERING, ['name'])

        def enrich(items):
            return {'fleet_stats': fleet_stats(org, [f.pk for f in items])}

        return list_response(
            request,
            queryset,
            FleetSerializer,
            enrich=enrich,
            always_enrich=True,
        )

    @extend_schema(
        summary='Create fleet',
        request=FleetSerializer,
        responses={201: FleetSerializer},
    )
    def post(self, request):
        org = get_user_org(request.user)
        serializer = FleetSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        fleet = serializer.save(organization=org)
        data = FleetSerializer(
            fleet,
            context={
                'request': request,
                'fleet_stats': fleet_stats(org, [fleet.pk]),
            },
        ).data
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary='Get fleet by ID',
        description='Includes `depots` (derived from vehicles) and `status_counts`.',
        responses={200: FleetSerializer, 404: None},
    ),
    patch=extend_schema(
        summary='Update fleet',
        request=FleetSerializer,
        responses={200: FleetSerializer, 404: None},
    ),
    delete=extend_schema(
        summary='Delete fleet',
        description=(
            'Deletes the fleet grouping only. Its vehicles are kept and simply '
            'become unassigned from any fleet.'
        ),
        responses={204: None, 404: None},
    ),
    put=extend_schema(exclude=True),
)
class FleetDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']
    serializer_class = FleetSerializer

    def get_queryset(self):
        return Fleet.objects.filter(
            organization=get_user_org(self.request.user)
        )

    def _present(self, fleet):
        org = get_user_org(self.request.user)
        data = FleetSerializer(
            fleet,
            context={
                'request': self.request,
                'fleet_stats': fleet_stats(org, [fleet.pk]),
            },
        ).data
        data.update(fleet_detail_extras(org, fleet))
        return data

    def retrieve(self, request, *args, **kwargs):
        return Response(self._present(self.get_object()))

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(self._present(instance))


class FleetMembershipView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Add and/or remove vehicles in a fleet',
        description=(
            'Atomic bulk membership change. `add` moves vehicles into this '
            'fleet (from any other); `remove` takes vehicles out. All ids must '
            'belong to the organization or nothing is changed.'
        ),
        request=FleetMembershipSerializer,
        responses={200: FleetSerializer},
    )
    def post(self, request, pk):
        org = get_user_org(request.user)
        try:
            fleet = Fleet.objects.get(pk=pk, organization=org)
        except Fleet.DoesNotExist:
            raise NotFound('Fleet not found.')
        serializer = FleetMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        add = set(serializer.validated_data.get('add', []))
        remove = set(serializer.validated_data.get('remove', []))
        if add & remove:
            raise ValidationError(
                'A vehicle cannot be both added and removed.'
            )

        with transaction.atomic():
            wanted = add | remove
            found = set(
                Vehicle.objects.select_for_update()
                .filter(organization=org, pk__in=wanted)
                .values_list('pk', flat=True)
            )
            missing = wanted - found
            if missing:
                raise ValidationError(
                    {
                        'add': [
                            f'{len(missing)} vehicle(s) not found in your organization.'
                        ]
                    }
                )
            Vehicle.objects.filter(organization=org, pk__in=add).update(
                fleet=fleet
            )
            Vehicle.objects.filter(
                organization=org, pk__in=remove, fleet=fleet
            ).update(fleet=None)

        data = FleetSerializer(
            fleet,
            context={
                'request': request,
                'fleet_stats': fleet_stats(org, [fleet.pk]),
            },
        ).data
        data.update(fleet_detail_extras(org, fleet))
        return Response(data)
