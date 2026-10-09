-- MySQL dump (legacy shared host, 2023) — DEPRECATED, FAKE demo data
DROP TABLE IF EXISTS `refunds`;
CREATE TABLE `refunds` (
  `id` int NOT NULL AUTO_INCREMENT,
  `customer_name` varchar(120) NOT NULL,
  `mobile` varchar(20) NOT NULL,
  `amount` decimal(10,2) NOT NULL,
  `gcash_ref` varchar(40) DEFAULT NULL,
  PRIMARY KEY (`id`)
);
INSERT INTO `refunds` VALUES
  (1,'Maria Clara Santos','0917-000-0001',450.00,'GC-0001'),
  (2,'Jose Rizal Mercado','0918-000-0002',1200.00,'GC-0002'),
  (3,'Andres Bonifacio Reyes','0919-000-0003',89.50,NULL);
