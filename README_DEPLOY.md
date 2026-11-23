# Despliegue para web pública

## Backend en Render

1. Crea una cuenta en Render.
2. Conecta este repositorio.
3. Crea un nuevo servicio web usando `render.yaml`.
4. Ajusta las variables de entorno.
5. Si usarás un dominio personalizado, asígnalo al servicio en Render y úsalo como `NEXT_PUBLIC_API_URL` en el frontend.

Ejemplo:

```
https://api.soland.com.ve
```

## Frontend en Daycohost

1. Conecta el repositorio del frontend y crea una aplicación Node.js.
2. Usa Node.js 20.9 o posterior, con estos comandos:

```
Build: npm ci && npm run build
Start: npm run start
```

3. Configura `NEXT_PUBLIC_API_URL` con la URL pública real del servicio backend en Render.
4. Asigna `soland.com.ve` y `www.soland.com.ve` a la aplicación desde el panel de Daycohost.

## Base de datos de producción

No uses SQLite en Render para producción. Configura `DB_URL` con una instancia MySQL externa usando el driver `mysql+aiomysql://` y deja `USE_SQLITE_FALLBACK=false`.

También debes configurar `SECRET`, `DEFAULT_ADMIN_PASSWORD` y `CORS_ALLOWED_ORIGINS` como secretos o variables del servicio. Nunca uses `admin123` fuera de desarrollo.

El manifiesto usa el plan gratuito de Render y no configura un disco persistente. El sistema de archivos es efímero: los documentos, fotos, facturas e inspecciones subidos pueden desaparecer al reiniciar o volver a desplegar el servicio. No guardes allí archivos importantes sin migrarlos antes a almacenamiento de objetos. La base de datos externa configurada en `DB_URL` puede tener costos propios. El plan gratuito también puede tardar en responder después de períodos de inactividad.

`BOOTSTRAP_ADMIN=false` evita restablecer la contraseña del administrador en cada arranque; actívalo sólo durante una inicialización controlada y vuelve a desactivarlo después.

El backend rechaza uploads mayores de `MAX_UPLOAD_MB` (15 MB por defecto) y sólo acepta extensiones permitidas por cada endpoint.

## CORS

`render.yaml` permite los orígenes públicos configurados para Soland. Si cambias de dominio, actualiza `CORS_ALLOWED_ORIGINS` y `FRONTEND_URL` en Render para que coincidan exactamente.

```
https://soland.com.ve
https://www.soland.com.ve
```

## Cloud Run

Cloud Run puede ejecutar el `Dockerfile` directamente. El contenedor ya escucha en `0.0.0.0:$PORT`.

1. Crea un proyecto en Google Cloud y habilita Cloud Run, Artifact Registry y Cloud Build.
2. Desde la carpeta `solandBackend`, construye y publica la imagen:

```bash
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/soland/soland-api
```

3. Despliega el servicio:

```bash
gcloud run deploy soland-api \
	--image REGION-docker.pkg.dev/PROJECT_ID/soland/soland-api \
	--region REGION \
	--platform managed \
	--allow-unauthenticated \
	--memory 2Gi \
	--cpu 2 \
	--timeout 300 \
	--min 0 \
	--max 2
```

4. Configura las variables de entorno desde Cloud Run. Como mínimo:

```text
APP_ENV=production
DB_URL=mysql+aiomysql://...
USE_SQLITE_FALLBACK=false
SECRET=...
DEFAULT_ADMIN_PASSWORD=...
BOOTSTRAP_ADMIN=true
CORS_ALLOWED_ORIGINS=https://soland.com,https://www.soland.com
MAX_UPLOAD_MB=15
```

Después del primer inicio y de confirmar el acceso del administrador, cambia `BOOTSTRAP_ADMIN` a `false`.

Cloud Run no conserva el sistema de archivos local. `MEDIA_ROOT` sólo sirve para archivos temporales en esta modalidad. Para conservar facturas, fotos e inspecciones hay que integrar Cloud Storage, Cloudinary o Supabase Storage antes de usarlo como producción definitiva.

Comprueba el servicio en `https://SERVICE_URL/health` y en `https://SERVICE_URL/docs`.
