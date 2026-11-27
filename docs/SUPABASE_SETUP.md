# Supabase / PostgreSQL para NBA TOTALS 2.0

Supabase proporciona PostgreSQL administrado. El adaptador usa conexión PostgreSQL directa con psycopg 3.

## 1. Crear la base de datos

1. Crea un proyecto en https://supabase.com.
2. Abre SQL Editor y ejecuta supabase/migrations/202611240001_nba_predictions.sql.
3. En Project Settings → Database → Connection string, copia la URI PostgreSQL. Para Vercel, usa el pooler de Supabase en modo Session si necesitas conectividad IPv4; utiliza la URI indicada por el panel.

## 2. Variables de entorno

En Vercel → Project → Settings → Environment Variables configura:

- SUPABASE_DB_URL: URI PostgreSQL completa, con usuario, contraseña, host, puerto y base.
- No uses la URL HTTPS del proyecto; debe ser una URI postgres:// o postgresql://.

Agrega la misma variable al entorno local mediante un archivo .env que NO se sube a Git. Nunca compartas ni publiques la contraseña.

## 3. Dependencia

El runtime instala psycopg[binary] desde requirements.txt. Para desarrollo editable también existe el extra postgres de pyproject.toml.

## 4. Estado de integración

PostgresPredictionTracker implementa la misma interfaz de PredictionTracker: record_prediction, settle_game y list_predictions. El factory create_prediction_tracker() selecciona PostgreSQL cuando encuentra SUPABASE_DB_URL o DATABASE_URL; si no, mantiene SQLite para desarrollo local.

Esta fase prepara el almacenamiento persistente. La API pública y el frontend aún no escriben automáticamente al tracker: eso requiere conectar los puntos de creación de predicción y de resultado final, además de proteger las rutas de escritura con una clave de servidor. No se expone ninguna credencial ni endpoint de escritura público en esta entrega.

## Seguridad

- Mantén RLS habilitado en la tabla.
- No pongas la URI de PostgreSQL en JavaScript, HTML ni variables públicas.
- Usa únicamente la variable privada de servidor en Vercel.
- La migración no habilita políticas anónimas de lectura/escritura.
- La conexión real queda pendiente hasta configurar la URI de Supabase en los secretos de Vercel.
