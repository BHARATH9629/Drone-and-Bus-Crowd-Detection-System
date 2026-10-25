const express = require('express');
const cors = require('cors');
const mysql = require('mysql2');
const path = require('path');

// Load environment variables (.env in current or root folder)
try {
  require('dotenv').config({ path: path.join(__dirname, '.env') });
  require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
} catch (_) {}

if (process.loadEnvFile) {
  try { process.loadEnvFile(path.join(__dirname, '.env')); } catch (_) {}
  try { process.loadEnvFile(path.join(__dirname, '..', '.env')); } catch (_) {}
}

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const DB_HOST = process.env.DB_HOST || 'localhost';
const DB_USER = process.env.DB_USER || 'root';
const DB_PASSWORD = process.env.DB_PASSWORD || '';
const DB_NAME = process.env.DB_NAME || 'busdb';

const db = mysql.createPool({
  host: DB_HOST,
  user: DB_USER,
  password: DB_PASSWORD,
  database: DB_NAME,
  waitForConnections: true,
  connectionLimit: 10,
});

db.getConnection((err, conn) => {
  if (err) {
    console.error('[DB ERROR] Cannot connect to MySQL:', err.message);
    console.error('[HINT] Ensure MySQL is running, check your .env settings, and run mysql_workbench_busdb.sql first.');
    return;
  }
  console.log(`[DB] Connected to MySQL database "${DB_NAME}"`);
  conn.release();
});

// Blynk IoT setup.
const BLYNK_AUTH_TOKEN = process.env.BLYNK_AUTH_TOKEN || '';
const BLYNK_BASE_URL = process.env.BLYNK_BASE_URL || 'https://blynk.cloud/external/api/update';
const BLYNK_CROWD_COUNT_PINS = ['V0'];

// Groq chatbot setup.
const GROQ_API_KEY = process.env.GROQ_API_KEY || '';
const GROQ_MODEL = process.env.GROQ_MODEL || 'llama-3.1-8b-instant';
const GROQ_CHAT_URL = process.env.GROQ_CHAT_URL || 'https://api.groq.com/openai/v1/chat/completions';

async function sendToBlynk(pin, value) {
  if (!BLYNK_AUTH_TOKEN) return;

  const url = new URL(BLYNK_BASE_URL);
  url.searchParams.set('token', BLYNK_AUTH_TOKEN);
  url.searchParams.set(pin, value ?? '');

  try {
    const response = await fetch(url);
    if (!response.ok) {
      console.error(`[BLYNK] ${pin} update failed: ${response.status}`);
    } else {
      console.log(`[BLYNK] ${pin} updated: ${value}`);
    }
  } catch (err) {
    console.error(`[BLYNK] ${pin} error:`, err.message);
  }
}

function updateBlynkDetection(data) {
  BLYNK_CROWD_COUNT_PINS.forEach((pin) => {
    sendToBlynk(pin, data.crowd_count);
  });
}

function updateBlynkGps(gps) {
  // GPS is kept in dashboard/MySQL. Blynk currently uses only V0 for crowd count.
}

async function getProjectStats() {
  const fallback = {
    average_people: null,
    max_people: null,
    total_samples: 0,
    latest_log: null,
    latest_gps: latestGps,
  };

  try {
    const [summaryRows] = await db.promise().query(
      `SELECT
         ROUND(AVG(people_count), 2) AS average_people,
         MAX(people_count) AS max_people,
         COUNT(*) AS total_samples
       FROM detection_logs`
    );
    const [latestRows] = await db.promise().query(
      `SELECT people_count, crowd_status, latitude, longitude, address, created_at
       FROM detection_logs
       ORDER BY created_at DESC
       LIMIT 1`
    );

    return {
      average_people: summaryRows[0]?.average_people ?? null,
      max_people: summaryRows[0]?.max_people ?? null,
      total_samples: summaryRows[0]?.total_samples ?? 0,
      latest_log: latestRows[0] ?? null,
      latest_gps: latestGps,
    };
  } catch (err) {
    console.error('[CHAT] Stats query error:', err.message);
    return fallback;
  }
}

let latestGps = {
  latitude: null,
  longitude: null,
  address: '',
  accuracy: null,
};

let latestData = {
  crowd_count: 0,
  crowd_status: 'CLEAR',
  detected_objects: 'none',
  sample_interval_seconds: 1,
  location: latestGps,
  timestamp: new Date().toISOString(),
};

app.post('/api/detection', (req, res) => {
  const crowdCount = Number(req.body.crowd_count ?? 0);
  const crowdStatus = req.body.crowd_status ?? 'CLEAR';
  const detectedObjects = req.body.detected_objects ?? 'none';
  const sampleInterval = Number(req.body.sample_interval_seconds ?? 1);

  latestData = {
    crowd_count: crowdCount,
    crowd_status: crowdStatus,
    detected_objects: detectedObjects,
    sample_interval_seconds: sampleInterval,
    location: latestGps,
    timestamp: new Date().toISOString(),
  };
  updateBlynkDetection(latestData);

  db.query(
    `INSERT INTO detection_logs
      (people_count, crowd_status, latitude, longitude, address, gps_accuracy, sample_interval_seconds)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [
      crowdCount,
      crowdStatus,
      latestGps.latitude,
      latestGps.longitude,
      latestGps.address,
      latestGps.accuracy,
      sampleInterval,
    ],
    (err) => {
      if (err) console.error('[DB] Detection insert error:', err.message);
    }
  );

  if (crowdCount >= 10) {
    db.query(
      `INSERT INTO alerts (alert_type, message, people_count, latitude, longitude, address)
       VALUES ('CROWD_DANGER', ?, ?, ?, ?, ?)`,
      [
        `Bus crowd reached ${crowdCount}`,
        crowdCount,
        latestGps.latitude,
        latestGps.longitude,
        latestGps.address,
      ],
      (err) => {
        if (err) console.error('[DB] Alert insert error:', err.message);
      }
    );
  }

  res.json({ ok: true });
});

app.post('/api/gps', (req, res) => {
  latestGps = {
    latitude: req.body.latitude ?? null,
    longitude: req.body.longitude ?? null,
    address: req.body.address ?? '',
    accuracy: req.body.accuracy ?? null,
  };
  latestData.location = latestGps;
  updateBlynkGps(latestGps);

  db.query(
    `INSERT INTO gps_logs (latitude, longitude, address, accuracy)
     VALUES (?, ?, ?, ?)`,
    [latestGps.latitude, latestGps.longitude, latestGps.address, latestGps.accuracy],
    (err) => {
      if (err) console.error('[DB] GPS insert error:', err.message);
    }
  );

  res.json({ ok: true });
});

app.get('/api/detection', (req, res) => res.json(latestData));

app.get('/api/logs', (req, res) => {
  db.query(
    `SELECT
       id,
       people_count,
       crowd_status,
       latitude,
       longitude,
       address,
       gps_accuracy,
       sample_interval_seconds,
       created_at
     FROM detection_logs
     ORDER BY created_at DESC
     LIMIT 50`,
    (err, rows) => {
      if (err) return res.status(500).json({ error: err.message });
      res.json(rows);
    }
  );
});

app.get('/api/alerts', (req, res) => {
  db.query(
    `SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50`,
    (err, rows) => {
      if (err) return res.status(500).json({ error: err.message });
      res.json(rows);
    }
  );
});

app.post('/api/chat', async (req, res) => {
  const question = String(req.body?.message ?? '').trim();
  if (!question) {
    return res.status(400).json({ error: 'Message is required.' });
  }

  if (!GROQ_API_KEY) {
    return res.json({
      reply: 'Groq API key is not set yet. Please set GROQ_API_KEY in your .env file.',
    });
  }

  const stats = await getProjectStats();
  const projectContext = {
    project: 'Bus crowd detection dashboard',
    camera_source: 'Phone IP camera or laptop webcam fallback',
    detector: 'YOLOv8 nano model, counts person detections and labels common objects',
    database: 'MySQL database busdb with detection_logs, gps_logs, and alerts tables',
    current_dashboard: latestData,
    database_stats: stats,
    blynk_pins: {
      V0: 'people count',
      V1: 'crowd status',
      V2: 'detected objects',
      V3: 'latitude',
      V4: 'longitude',
      V5: 'address',
    },
  };

  try {
    const groqResponse = await fetch(GROQ_CHAT_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: GROQ_MODEL,
        temperature: 0.3,
        max_tokens: 700,
        messages: [
          {
            role: 'system',
            content:
              'You are a helpful assistant inside a bus crowd detection dashboard. ' +
              'Answer questions about this project, its MySQL database, latest crowd values, GPS/location, object detection, Blynk pins, and general bus/travel topics. ' +
              'Use the provided project context for live project/database answers. ' +
              'For travel questions, give practical approximate guidance and say when exact live fare/schedule data should be checked in an official app.',
          },
          {
            role: 'system',
            content: `Project context JSON: ${JSON.stringify(projectContext)}`,
          },
          { role: 'user', content: question },
        ],
      }),
    });

    const data = await groqResponse.json();
    if (!groqResponse.ok) {
      return res.status(502).json({
        error: data.error?.message || `Groq request failed with ${groqResponse.status}`,
      });
    }

    res.json({
      reply: data.choices?.[0]?.message?.content || 'No answer returned from Groq.',
    });
  } catch (err) {
    console.error('[CHAT] Groq error:', err.message);
    res.status(500).json({ error: 'Chatbot failed to contact Groq.' });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Bus dashboard running at http://localhost:${PORT}`);
});
