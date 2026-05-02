-- ============================================
-- Smart Parking System - Database Schema
-- ============================================

CREATE DATABASE IF NOT EXISTS smart_parking;
USE smart_parking;

-- -------------------------------------------
-- Table: parking_slots
-- Stores info about each parking slot
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS parking_slots (
    slot_id       INT PRIMARY KEY AUTO_INCREMENT,
    slot_label    VARCHAR(10) NOT NULL UNIQUE,
    x1            INT NOT NULL DEFAULT 0,
    y1            INT NOT NULL DEFAULT 0,
    x2            INT NOT NULL DEFAULT 0,
    y2            INT NOT NULL DEFAULT 0,
    is_occupied   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- -------------------------------------------
-- Table: reservations
-- Stores user booking details
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id  INT PRIMARY KEY AUTO_INCREMENT,
    user_name       VARCHAR(100) NOT NULL,
    car_plate       VARCHAR(20)  NULL,
    user_email      VARCHAR(255) NULL,
    slot_id         INT NOT NULL,
    start_time      DATETIME NOT NULL,
    end_time        DATETIME NOT NULL,
    status          ENUM('active', 'confirmed', 'completed', 'cancelled') DEFAULT 'active',
    qr_verified_at  DATETIME NULL,
    grace_warning_sent BOOLEAN DEFAULT FALSE,
    end_reminder_sent  BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (slot_id) REFERENCES parking_slots(slot_id)
);

-- Run this if the table already exists (one-time migration):
-- ALTER TABLE reservations
--   ADD COLUMN car_plate  VARCHAR(20)  NULL AFTER user_name,
--   ADD COLUMN user_email VARCHAR(255) NULL AFTER car_plate;

-- -------------------------------------------
-- Table: overstay_violations
-- Logs when a vehicle overstays its booking
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS overstay_violations (
    violation_id    INT PRIMARY KEY AUTO_INCREMENT,
    reservation_id  INT NOT NULL,
    slot_id         INT NOT NULL,
    user_name       VARCHAR(100) NOT NULL,
    expected_end    DATETIME NOT NULL,
    detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved        BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(slot_id)
);

-- -------------------------------------------
-- Table: occupancy_history
-- Stores historical occupancy snapshots
-- -------------------------------------------
CREATE TABLE IF NOT EXISTS occupancy_history (
    record_id       INT PRIMARY KEY AUTO_INCREMENT,
    timestamp       DATETIME NOT NULL,
    total_slots     INT NOT NULL,
    occupied_slots  INT NOT NULL,
    available_slots INT NOT NULL,
    hour_of_day     INT NOT NULL,
    day_of_week     INT NOT NULL
);

-- Seed: Insert 42 parking slots mapped from parking_feed.mkv
-- -------------------------------------------
INSERT INTO parking_slots (slot_label, x1, y1, x2, y2, is_occupied) VALUES
('A1', 15,  760, 120, 920, FALSE),
('A2', 125, 740, 230, 900, FALSE),
('A3', 235, 720, 340, 880, FALSE),
('A4', 345, 700, 450, 860, FALSE),
('A5', 455, 680, 555, 840, FALSE),
('A6', 560, 665, 660, 820, FALSE),
('B1', 15,  560, 110, 700, FALSE),
('B2', 115, 545, 210, 685, FALSE),
('B3', 215, 530, 310, 665, FALSE),
('B4', 315, 515, 410, 650, FALSE),
('B5', 415, 500, 510, 635, FALSE),
('B6', 515, 485, 610, 620, FALSE),
('C1', 720, 640, 830, 790, FALSE),
('C2', 835, 625, 945, 775, FALSE),
('C3', 950, 610, 1060, 760, FALSE),
('C4', 1065, 595, 1175, 745, FALSE),
('C5', 1180, 580, 1290, 730, FALSE),
('C6', 1295, 565, 1400, 715, FALSE),
('D1', 720, 460, 820, 590, FALSE),
('D2', 825, 450, 925, 580, FALSE),
('D3', 930, 440, 1030, 570, FALSE),
('D4', 1035, 430, 1130, 555, FALSE),
('D5', 1135, 420, 1230, 545, FALSE),
('D6', 1235, 410, 1330, 535, FALSE),
('E1', 1350, 550, 1460, 700, FALSE),
('E2', 1465, 540, 1570, 690, FALSE),
('E3', 1575, 530, 1680, 680, FALSE),
('E4', 1685, 520, 1790, 670, FALSE),
('F1', 1400, 400, 1500, 520, FALSE),
('F2', 1505, 390, 1600, 510, FALSE),
('F3', 1605, 380, 1700, 500, FALSE),
('F4', 1705, 370, 1800, 490, FALSE),
('G1', 30,  910, 150, 1070, FALSE),
('G2', 160, 890, 280, 1050, FALSE),
('G3', 290, 870, 410, 1030, FALSE),
('G4', 420, 855, 540, 1010, FALSE),
('G5', 550, 840, 670, 995, FALSE),
('G6', 680, 825, 800, 980, FALSE),
('G7', 810, 810, 930, 965, FALSE),
('G8', 940, 795, 1060, 950, FALSE),
('G9', 1070, 780, 1190, 935, FALSE),
('G10', 1200, 768, 1320, 920, FALSE);
