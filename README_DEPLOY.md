# Despliegue para web pública

## Backend en Render

1. Crea una cuenta en Render.
2. Conecta este repositorio.
3. Crea un nuevo servicio web usando `render.yaml`.
4. Ajusta las variables de entorno.
5. Usa la URL generada por Render como `NEXT_PUBLIC_API_URL` en el frontend.

Ejemplo:

```
https://soland-api.onrender.com
```

## Frontend en Vercel

1. Importa el proyecto de frontend a Vercel.
2. Usa estas variables:

```
NEXT_PUBLIC_API_URL=https://soland-api.onrender.com
```

3. Haz deploy.

## Dominio

Si quieres usar `soland.com`:

- apunta el dominio principal a Vercel
- apunta `api.soland.com` a la URL de Render

## CORS

El backend ya está preparado para aceptar varios orígenes desde:

```
https://soland.com
https://www.soland.com
```
