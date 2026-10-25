CREATE DATABASE IF NOT EXISTS busdb;
USE busdb;

CREATE TABLE IF NOT EXISTS detection_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  people_count INT NOT NULL DEFAULT 0,
  crowd_status VARCHAR(20) NOT NULL DEFAULT 'CLEAR',
  latitude DECIMAL(10, 6) NULL,
  longitude DECIMAL(10, 6) NULL,
  address TEXT,
  gps_accuracy DECIMAL(10, 2) NULL,
  sample_interval_seconds INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gps_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  latitude DECIMAL(10, 6) NULL,
  longitude DECIMAL(10, 6) NULL,
  address TEXT,
  accuracy DECIMAL(10, 2) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  alert_type VARCHAR(50) NOT NULL,
  message TEXT NOT NULL,
  people_count INT NOT NULL DEFAULT 0,
  latitude DECIMAL(10, 6) NULL,
  longitude DECIMAL(10, 6) NULL,
  address TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

SELECT
  id,
  people_count,
  crowd_status,
  latitude,
  longitude,
  address,
  sample_interval_seconds,
  created_at AS reading_time
FROM detection_logs
ORDER BY created_at DESC;
