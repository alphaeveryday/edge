package com.edge.superadmin;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@ConfigurationPropertiesScan
@SpringBootApplication
public class SuperAdminApplication {

	public static void main(String[] args) {
		SpringApplication.run(SuperAdminApplication.class, args);
	}

}
