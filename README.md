# EVORA Engine

Backend API for **EVORA – AI-powered Enterprise Fleet Management Platform** built with **Django 6.0 + Django REST Framework + PostGIS**.

---

## 🎯 What is EVORA Engine?

EVORA Engine is the core backend service that powers the EVORA fleet management system. It provides REST APIs for managing fleet operations including vehicles, drivers, depots, stops, job templates, and job instances. The backend handles user authentication via Supabase, manages complex geospatial queries, and maintains all business logic for fleet optimization.

**Developed for:** ET AI Hackathon 2026

---

## 🏗️ Architecture Overview

EVORA Engine is part of a larger distributed system:

```
┌──────────────────────────────────┐
│   EVORA Web Frontend             │
│   React 19 + TypeScript + Vite   │
│   Port: 5173                     │
└───────────────┬──────────────────┘
                │ REST API (Axios)
                │ Authentication (Supabase)
                ▼
┌──────────────────────────────────┐
│   EVORA Engine Backend           │
│   (You are here)                 │
│   Django 6.0 + DRF + PostGIS    │
│   Port: 8000                     │
└───────────────┬──────────────────┘
                │
        ┌───────┼───────┐
        ▼       ▼       ▼
     PostgreSQL PostGIS Supabase
```

---

## 🛠️ Tech Stack

### Core Framework
- **Django** 6.0 — Web framework
- **Django REST Framework (DRF)** — REST API framework
- **Python** 3.x — Backend language

### Database & Geospatial
- **PostgreSQL** 16 — Relational database
- **PostGIS** 3.4 — Geospatial extension for location-based queries
- **django.contrib.gis** — Django ORM with geospatial support

### API Documentation
- **drf-spectacular** — Automatic OpenAPI/Swagger documentation generation

### Authentication
- **Supabase** — External user authentication
- Custom `SupabaseAuthentication` class — JWT token validation

### Containerization
- **Docker** — Container runtime
- **Docker Compose** — Multi-container orchestration

---

## 📁 Project Structure

```
app/
├── manage.py                           # Django management CLI
├── app/
│   ├── settings.py                    # Django configuration
│   ├── urls.py                        # URL routing
│   ├── wsgi.py                        # WSGI application
│   └── asgi.py                        # ASGI application
├── users/                             # User authentication app
│   ├── models.py                      # User model
│   ├── views.py                       # User endpoints
│   ├── serializers.py                 # Request/response serialization
│   ├── authentication.py              # Supabase JWT validation
│   └── urls.py                        # User routes
├── organizations/                     # Organization/company app
│   ├── models.py                      # Organization model
│   ├── views.py                       # Organization endpoints
│   ├── serializers.py
│   └── urls.py
├── vehicles/                          # Fleet vehicles app
│   ├── models.py                      # Vehicle model
│   ├── views.py                       # Vehicle endpoints
│   └── serializers.py
├── drivers/                           # Driver management app
│   ├── models.py                      # Driver model
│   ├── views.py                       # Driver endpoints
│   └── serializers.py
├── depots/                            # Depot/warehouse app
│   ├── models.py                      # Depot model with location
│   ├── views.py                       # Depot endpoints
│   └── serializers.py
├── stops/                             # Delivery/pickup stops app
│   ├── models.py                      # Stop model with geolocation
│   ├── views.py                       # Stop endpoints
│   └── serializers.py
├── job_templates/                     # Job template definitions
│   ├── models.py                      # JobTemplate model
│   ├── views.py                       # JobTemplate endpoints
│   └── serializers.py
├── job_instances/                     # Actual job instances
│   ├── models.py                      # JobInstance model
│   ├── views.py                       # JobInstance endpoints
│   └── serializers.py
├── instance_stops/                    # Stops within a job instance
│   ├── models.py                      # InstanceStop model (junction)
│   ├── views.py                       # InstanceStop endpoints
│   └── serializers.py
└── deploy/
    └── docker/
        ├── docker-compose.yaml        # Multi-container setup
        └── Dockerfile                 # App container image
```

---

## 🚀 Getting Started

### Prerequisites

- **Docker** (v20+)
- **Docker Compose** (v1.29+)
- **Git**

Verify installation:

```bash
docker --version      # Docker version 20.x or higher
docker compose version # Docker Compose version 1.29 or higher
git --version
```

### 1. Clone the Repository

```bash
git clone https://github.com/EVORA-ET/Evora-engine.git
cd Evora-engine
```

### 2. Checkout Development Branch

```bash
git checkout dev
```

### 3. Start Services with Docker Compose

```bash
docker compose -f deploy/docker/docker-compose.yaml up -d
```

This starts:
- **Django app** on `http://localhost:8000`
- **PostgreSQL** on `localhost:5432`
- **PostGIS** geospatial extension enabled

### 4. Verify Services are Running

```bash
docker compose -f deploy/docker/docker-compose.yaml ps
```

Expected output:
```
NAME         STATUS
app          Up X seconds
db           Up X seconds
```

### 5. Test the API

Visit the API root:

```bash
curl http://localhost:8000/api/
```

Or open in browser: `http://localhost:8000/api/`

---

## 📝 Available Commands

### Service Management

```bash
# Start all services in background
docker compose -f deploy/docker/docker-compose.yaml up -d

# Stop all services
docker compose -f deploy/docker/docker-compose.yaml down

# View logs
docker compose -f deploy/docker/docker-compose.yaml logs -f app

# Rebuild app image
docker compose -f deploy/docker/docker-compose.yaml build

# View running containers
docker compose -f deploy/docker/docker-compose.yaml ps
```

### Django Management Commands

```bash
# Run migrations
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py migrate"

# Create superuser (admin)
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py createsuperuser"

# Create a new Django app
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py startapp <app_name>"

# Collect static files
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py collectstatic --noinput"

# Run Django shell
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py shell"

# Make migrations
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py makemigrations"
```

---

## 🗄️ Database Models

### Core Domain Models

#### Users (`users` app)
```python
User
├── email
├── password_hash
├── first_name
├── last_name
├── created_at
└── updated_at
```

#### Organizations (`organizations` app)
```python
Organization
├── name
├── email
├── phone
├── address
├── created_by (FK: User)
└── created_at
```

#### Vehicles (`vehicles` app)
```python
Vehicle
├── registration_number
├── vehicle_type
├── capacity
├── organization (FK: Organization)
├── driver (FK: Driver, nullable)
└── created_at
```

#### Drivers (`drivers` app)
```python
Driver
├── name
├── license_number
├── phone
├── email
├── organization (FK: Organization)
├── status (active/inactive)
└── created_at
```

#### Depots (`depots` app)
```python
Depot
├── name
├── address
├── location (GeometryField - Point)  # PostGIS
├── phone
├── organization (FK: Organization)
└── created_at
```

#### Stops (`stops` app)
```python
Stop
├── name
├── address
├── location (GeometryField - Point)  # PostGIS
├── type (pickup/delivery/waypoint)
├── contact_name
├── contact_phone
├── organization (FK: Organization)
└── created_at
```

#### Job Templates (`job_templates` app)
```python
JobTemplate
├── name
├── description
├── template_stops (M2M: Stop)
├── estimated_duration
├── organization (FK: Organization)
└── created_at
```

#### Job Instances (`job_instances` app)
```python
JobInstance
├── name
├── job_template (FK: JobTemplate)
├── assigned_vehicle (FK: Vehicle)
├── assigned_driver (FK: Driver)
├── status (pending/in_progress/completed/cancelled)
├── start_time
├── end_time
├── organization (FK: Organization)
└── created_at
```

#### Instance Stops (`instance_stops` app)
```python
InstanceStop
├── job_instance (FK: JobInstance)
├── stop (FK: Stop)
├── sequence_number
├── arrival_time
├── departure_time
└── status (pending/reached/completed)
```

---

## 🔐 Authentication

### Supabase Integration

The backend validates JWT tokens issued by Supabase:

1. **Frontend** authenticates user with Supabase (email/password)
2. **Supabase** issues JWT token and stores in session
3. **Frontend** sends token in `Authorization: Bearer <token>` header
4. **Backend** validates token using custom `SupabaseAuthentication` class
5. **User identity** extracted from token claims
6. **Endpoint** proceeds if token is valid, rejects otherwise

### Authentication in Settings

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'users.authentication.SupabaseAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}
```

**Result:** All endpoints require valid Supabase JWT token

---

## 📡 API Endpoints

### Automatic Documentation

Visit Swagger UI (auto-generated by drf-spectacular):

```
http://localhost:8000/api/schema/swagger-ui/
```

### Example Endpoints

#### Users
```
POST   /api/users/                    # Create user
GET    /api/users/me/                 # Current user profile
PUT    /api/users/me/                 # Update profile
```

#### Organizations
```
POST   /api/organizations/            # Create organization
GET    /api/organizations/            # List organizations
GET    /api/organizations/{id}/       # Organization details
PUT    /api/organizations/{id}/       # Update organization
```

#### Vehicles
```
POST   /api/vehicles/                 # Add vehicle
GET    /api/vehicles/                 # List vehicles
GET    /api/vehicles/{id}/            # Vehicle details
PUT    /api/vehicles/{id}/            # Update vehicle
DELETE /api/vehicles/{id}/            # Remove vehicle
```

#### Drivers
```
POST   /api/drivers/                  # Register driver
GET    /api/drivers/                  # List drivers
GET    /api/drivers/{id}/             # Driver details
PUT    /api/drivers/{id}/             # Update driver
```

#### Depots
```
POST   /api/depots/                   # Create depot
GET    /api/depots/                   # List depots
GET    /api/depots/{id}/              # Depot details
GET    /api/depots/nearby/?lat=<>&lng=<>&radius=<>  # Geospatial query
```

#### Stops
```
POST   /api/stops/                    # Create stop
GET    /api/stops/                    # List stops
GET    /api/stops/{id}/               # Stop details
GET    /api/stops/nearby/?lat=<>&lng=<>&radius=<>  # Geospatial query
```

#### Job Templates
```
POST   /api/job-templates/            # Create template
GET    /api/job-templates/            # List templates
GET    /api/job-templates/{id}/       # Template details
```

#### Job Instances
```
POST   /api/job-instances/            # Create job
GET    /api/job-instances/            # List jobs
GET    /api/job-instances/{id}/       # Job details
PUT    /api/job-instances/{id}/       # Update job (status, assignments)
```

---

## 🌊 Request/Response Flow Example

### Create an Organization

**Request:**
```bash
curl -X POST http://localhost:8000/api/organizations/ \
  -H "Authorization: Bearer <supabase_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Logistics",
    "email": "contact@acmelogistics.com",
    "phone": "+1-555-1234",
    "address": "123 Fleet Ave, City, State"
  }'
```

**Response:**
```json
{
  "id": 1,
  "name": "Acme Logistics",
  "email": "contact@acmelogistics.com",
  "phone": "+1-555-1234",
  "address": "123 Fleet Ave, City, State",
  "created_by": "user-uuid",
  "created_at": "2026-07-22T10:30:00Z"
}
```

### Geospatial Query: Find Nearby Depots

```bash
curl -X GET "http://localhost:8000/api/depots/nearby/?lat=40.7128&lng=-74.0060&radius=50" \
  -H "Authorization: Bearer <supabase_token>"
```

**Response:**
```json
[
  {
    "id": 1,
    "name": "Downtown Depot",
    "address": "456 Central St",
    "location": {
      "type": "Point",
      "coordinates": [40.7130, -74.0061]
    },
    "distance_km": 0.12
  },
  {
    "id": 2,
    "name": "Warehouse B",
    "address": "789 Industrial Blvd",
    "location": {
      "type": "Point",
      "coordinates": [40.7400, -74.0100]
    },
    "distance_km": 3.5
  }
]
```

---

## ⚙️ Environment Variables

Docker Compose loads from environment:

```env
DB_HOST=db                    # Database container name
DB_NAME=devdb                 # Database name
DB_USER=devuser               # Database user
DB_PASS=changeme              # Database password (change in production!)

SUPABASE_PROJECT_URL=https://xxxx.supabase.co  # Supabase project URL
```

**Override in `docker-compose.yaml`:**

```yaml
environment:
  - DB_HOST=db
  - DB_NAME=devdb
  - DB_USER=devuser
  - DB_PASS=changeme
  - SUPABASE_PROJECT_URL=https://xxxx.supabase.co
```

---

## 🌍 PostGIS Geospatial Features

The backend uses PostGIS for location-based queries:

### Point Geometry (Depots & Stops)
```python
from django.contrib.gis.geos import Point

depot.location = Point(40.7128, -74.0060)  # lat, lng
```

### Distance Queries
```python
from django.contrib.gis.db.models.functions import Distance
from django.db.models import F

nearby = Depot.objects.annotate(
    distance=Distance('location', Point(40.7128, -74.0060))
).filter(
    distance__lte=50000  # 50km in meters
).order_by('distance')
```

### Use Cases
- Find depots within radius
- Optimize delivery routes
- Calculate travel distances
- Geofencing alerts
- Zone-based operations

---

## 🧪 Testing

### Access Django Shell

```bash
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py shell"
```

### Create Test Data

```python
from organizations.models import Organization
from users.models import User

# Create organization
org = Organization.objects.create(
    name="Test Company",
    email="test@example.com"
)

print(org)
```

### Run Tests (if tests exist)

```bash
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py test"
```

---

## 🐛 Troubleshooting

### 1. Port 8000 already in use

Change the port mapping in `docker-compose.yaml`:

```yaml
ports:
  - "8001:8000"  # Host:Container
```

Then access at `http://localhost:8001`

### 2. Database connection failed

Check if PostgreSQL is running:

```bash
docker compose -f deploy/docker/docker-compose.yaml logs db
```

Verify `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS` in environment

### 3. Supabase authentication failing

1. Verify `SUPABASE_PROJECT_URL` is set
2. Check Supabase project is active
3. Ensure frontend is sending valid JWT token
4. Check token expiration

### 4. Migrations not applied

Manually run migrations:

```bash
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py migrate"
```

### 5. PostGIS extension not loaded

Check database logs:

```bash
docker compose -f deploy/docker/docker-compose.yaml logs db
```

Ensure `postgis/postgis:16-3.4` image is used (it includes PostGIS)

---

## 📋 Development Workflow

### Start a New Feature

```bash
# Pull latest
git checkout dev
git pull origin dev

# Create feature branch
git checkout -b feature/<feature-name>
```

### Make Changes & Test

```bash
# Run migrations if models changed
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py makemigrations"
docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py migrate"

# Test API endpoints with curl or Postman
# Use Swagger UI: http://localhost:8000/api/schema/swagger-ui/
```

### Commit & Push

```bash
git add .
git commit -m "Add feature description"
git push origin feature/<feature-name>
```

### Create Pull Request

1. Go to GitHub repository
2. Click **"New Pull Request"**
3. Select `feature/<feature-name>` → `dev`
4. Add description
5. Request review
6. Merge once approved

---

## 📚 Key Concepts

### Django Apps
Modular components, each handling a domain:
- `users` — Authentication
- `organizations` — Companies
- `vehicles` — Fleet management
- `depots` — Warehouse locations
- `stops` — Delivery points
- `job_instances` — Actual jobs

### REST Framework Serializers
Convert Python objects ↔ JSON:
```python
class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'email', 'phone', 'address', 'created_at']
```

### ViewSets
Auto-generate CRUD endpoints:
```python
class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
```

### PostGIS GeometryField
Store and query geographic locations:
```python
location = gis_models.PointField(null=True, blank=True)
```

---

## 🔗 Related Repositories

- **[EVORA Web](https://github.com/EVORA-ET/evora-web)** — Frontend React application
- **[Documentation](https://github.com/EVORA-ET/Documentations)** — Project documentation

---

## 🤝 Contributing

1. Always work on the **dev** branch
2. Create feature branches for new work
3. Follow Django/DRF conventions
4. Write meaningful commit messages
5. Test locally before pushing
6. Create PRs for code review

---

## 📞 Support & Documentation

- **Django Docs:** https://docs.djangoproject.com/
- **DRF Docs:** https://www.django-rest-framework.org/
- **PostGIS Docs:** https://postgis.net/documentation/
- **drf-spectacular:** https://drf-spectacular.readthedocs.io/

---

## 📝 Notes

- Hot reload enabled — code changes take effect immediately
- Database data persists in Docker volume (`dev-db-data`)
- All development on the **dev** branch
- Production requires secure credentials (NOT the dev defaults)
- CORS should be configured for production deployment

---

## 🎯 Project Status

**Status:** Under Active Development (ET AI Hackathon 2026)

**Last Updated:** July 2026

**Contributors:** EVORA Team


