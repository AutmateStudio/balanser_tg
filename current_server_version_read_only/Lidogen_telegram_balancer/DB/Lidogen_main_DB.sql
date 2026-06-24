-- DROP SCHEMA public;

CREATE SCHEMA public AUTHORIZATION pg_database_owner;

-- DROP TYPE public."account_status";

CREATE TYPE public."account_status" AS ENUM (
	'active',
	'cooldown',
	'disabled',
	'banned',
	'error');

-- DROP TYPE public."attempt_status";

CREATE TYPE public."attempt_status" AS ENUM (
	'success',
	'error',
	'timeout');

-- DROP TYPE public."billing_invoice_status";

CREATE TYPE public."billing_invoice_status" AS ENUM (
	'draft',
	'issued',
	'pending',
	'paid',
	'canceled',
	'expired',
	'failed');

-- DROP TYPE public."billing_payment_status";

CREATE TYPE public."billing_payment_status" AS ENUM (
	'created',
	'pending',
	'authorized',
	'confirmed',
	'paid',
	'failed',
	'refunded');

-- DROP TYPE public."billing_period";

CREATE TYPE public."billing_period" AS ENUM (
	'month',
	'year',
	'one_time',
	'custom');

-- DROP TYPE public."binding_status";

CREATE TYPE public."binding_status" AS ENUM (
	'pending',
	'used',
	'expired',
	'canceled');

-- DROP TYPE public."client_feedback_status";

CREATE TYPE public."client_feedback_status" AS ENUM (
	'unknown',
	'lead',
	'not_lead');

-- DROP TYPE public."delivery_status";

CREATE TYPE public."delivery_status" AS ENUM (
	'pending',
	'sent',
	'failed',
	'skipped');

-- DROP TYPE public.gbtreekey16;

CREATE TYPE public.gbtreekey16 (
	INPUT = gbtreekey16_in,
	OUTPUT = gbtreekey16_out,
	INTERNALLENGTH = 16,
	ALIGNMENT = 4,
	STORAGE = plain,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public.gbtreekey2;

CREATE TYPE public.gbtreekey2 (
	INPUT = gbtreekey2_in,
	OUTPUT = gbtreekey2_out,
	INTERNALLENGTH = 2,
	ALIGNMENT = 4,
	STORAGE = plain,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public.gbtreekey32;

CREATE TYPE public.gbtreekey32 (
	INPUT = gbtreekey32_in,
	OUTPUT = gbtreekey32_out,
	INTERNALLENGTH = 32,
	ALIGNMENT = 4,
	STORAGE = plain,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public.gbtreekey4;

CREATE TYPE public.gbtreekey4 (
	INPUT = gbtreekey4_in,
	OUTPUT = gbtreekey4_out,
	INTERNALLENGTH = 4,
	ALIGNMENT = 4,
	STORAGE = plain,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public.gbtreekey8;

CREATE TYPE public.gbtreekey8 (
	INPUT = gbtreekey8_in,
	OUTPUT = gbtreekey8_out,
	INTERNALLENGTH = 8,
	ALIGNMENT = 4,
	STORAGE = plain,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public.gbtreekey_var;

CREATE TYPE public.gbtreekey_var (
	INPUT = gbtreekey_var_in,
	OUTPUT = gbtreekey_var_out,
	ALIGNMENT = 4,
	STORAGE = any,
	CATEGORY = U,
	DELIMITER = ',');

-- DROP TYPE public."notification_type";

CREATE TYPE public."notification_type" AS ENUM (
	'new_lead',
	'system_report',
	'billing_paid',
	'billing_failed',
	'subscription_expiring',
	'channel_request_approved',
	'channel_request_rejected',
	'subscription_renewed',
	'balance_topup_reminder');

-- DROP TYPE public."payment_account_auth_type";

CREATE TYPE public."payment_account_auth_type" AS ENUM (
	'terminal',
	'jwt',
	'oauth2',
	'api_key',
	'manual');

-- DROP TYPE public."payment_provider_code";

CREATE TYPE public."payment_provider_code" AS ENUM (
	'tbank',
	'tochka',
	'manual',
	'other');

-- DROP TYPE public."plan_scope";

CREATE TYPE public."plan_scope" AS ENUM (
	'public',
	'custom');

-- DROP TYPE public."project_status";

CREATE TYPE public."project_status" AS ENUM (
	'active',
	'archived',
	'deleted',
	'draft',
	'subscription_expired',
	'subscription_canceled');

-- DROP TYPE public."promo_code_type";

CREATE TYPE public."promo_code_type" AS ENUM (
	'free_plan',
	'discount_percent',
	'discount_fixed');

-- DROP TYPE public."screening_run_status";

CREATE TYPE public."screening_run_status" AS ENUM (
	'queued',
	'processing',
	'completed',
	'failed',
	'skipped');

-- DROP TYPE public."source_channel_request_status";

CREATE TYPE public."source_channel_request_status" AS ENUM (
	'new',
	'processing',
	'approved',
	'rejected',
	'canceled');

-- DROP TYPE public."subscription_status";

CREATE TYPE public."subscription_status" AS ENUM (
	'pending',
	'active',
	'paused',
	'canceled',
	'expired');

-- DROP TYPE public."task_op_account_role";

CREATE TYPE public."task_op_account_role" AS ENUM (
	'primary',
	'source',
	'target');

-- DROP TYPE public."task_status";

CREATE TYPE public."task_status" AS ENUM (
	'queued',
	'scheduled',
	'in_progress',
	'retry',
	'done',
	'failed',
	'cancelled',
	'stuck');

-- DROP TYPE public."user_notification_channel_status";

CREATE TYPE public."user_notification_channel_status" AS ENUM (
	'pending',
	'active',
	'paused',
	'blocked',
	'invalid');

-- DROP TYPE public."user_status";

CREATE TYPE public."user_status" AS ENUM (
	'active',
	'blocked',
	'deleted');

-- DROP TYPE public."verification_token_type";

CREATE TYPE public."verification_token_type" AS ENUM (
	'email_verification',
	'password_reset');

-- DROP TYPE public."webhook_process_status";

CREATE TYPE public."webhook_process_status" AS ENUM (
	'new',
	'processed',
	'ignored',
	'failed');

-- DROP SEQUENCE public.account_resource_usage_id_seq;

CREATE SEQUENCE public.account_resource_usage_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.accounts_id_seq;

CREATE SEQUENCE public.accounts_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.analytics_daily_snapshots_id_seq;

CREATE SEQUENCE public.analytics_daily_snapshots_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.audit_logs_id_seq;

CREATE SEQUENCE public.audit_logs_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.billing_invoices_id_seq;

CREATE SEQUENCE public.billing_invoices_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.billing_payments_id_seq;

CREATE SEQUENCE public.billing_payments_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.billing_requisites_id_seq;

CREATE SEQUENCE public.billing_requisites_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.billing_webhook_events_id_seq;

CREATE SEQUENCE public.billing_webhook_events_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.contact_channel_bindings_id_seq;

CREATE SEQUENCE public.contact_channel_bindings_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.in_app_notifications_id_seq;

CREATE SEQUENCE public.in_app_notifications_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.message_ai_screening_runs_id_seq;

CREATE SEQUENCE public.message_ai_screening_runs_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.monitoring_projects_id_seq;

CREATE SEQUENCE public.monitoring_projects_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.payment_provider_accounts_id_seq;

CREATE SEQUENCE public.payment_provider_accounts_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.platforms_id_seq;

CREATE SEQUENCE public.platforms_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.project_subscriptions_id_seq;

CREATE SEQUENCE public.project_subscriptions_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.promo_code_usages_id_seq;

CREATE SEQUENCE public.promo_code_usages_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.promo_codes_id_seq;

CREATE SEQUENCE public.promo_codes_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.resource_op_types_id_seq;

CREATE SEQUENCE public.resource_op_types_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.roles_id_seq;

CREATE SEQUENCE public.roles_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.source_channel_requests_id_seq;

CREATE SEQUENCE public.source_channel_requests_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.source_channels_id_seq;

CREATE SEQUENCE public.source_channels_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.source_messages_id_seq;

CREATE SEQUENCE public.source_messages_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.subscription_plans_id_seq;

CREATE SEQUENCE public.subscription_plans_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.task_attempts_id_seq;

CREATE SEQUENCE public.task_attempts_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.task_queue_id_seq;

CREATE SEQUENCE public.task_queue_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.task_type_ops_id_seq;

CREATE SEQUENCE public.task_type_ops_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.task_types_id_seq;

CREATE SEQUENCE public.task_types_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.user_contact_channels_id_seq;

CREATE SEQUENCE public.user_contact_channels_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.user_notification_channels_id_seq;

CREATE SEQUENCE public.user_notification_channels_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.user_wallets_id_seq;

CREATE SEQUENCE public.user_wallets_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.users_id_seq;

CREATE SEQUENCE public.users_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.verification_tokens_id_seq;

CREATE SEQUENCE public.verification_tokens_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.wallet_transactions_id_seq;

CREATE SEQUENCE public.wallet_transactions_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE public.webhook_outbox_id_seq;

CREATE SEQUENCE public.webhook_outbox_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;-- public._migrations_applied определение

-- Drop table

-- DROP TABLE public._migrations_applied;

CREATE TABLE public._migrations_applied (
	"name" varchar(255) NOT NULL,
	applied_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT _migrations_applied_pkey PRIMARY KEY (name)
);


-- public.billing_requisites определение

-- Drop table

-- DROP TABLE public.billing_requisites;

CREATE TABLE public.billing_requisites (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	title varchar NOT NULL,
	legal_name varchar NOT NULL,
	inn varchar NULL,
	kpp varchar NULL,
	ogrn varchar NULL,
	bank_name varchar NULL,
	bik varchar NULL,
	checking_account varchar NULL,
	correspondent_account varchar NULL,
	legal_address text NULL,
	signer_name varchar NULL,
	signer_position varchar NULL,
	vat_mode varchar NULL,
	is_default bool DEFAULT false NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT billing_requisites_pkey PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_billing_requisites_default ON public.billing_requisites USING btree (is_default) WHERE (is_default = true);

-- Table Triggers

create trigger trg_billing_requisites_update_updated_at before
update
    on
    public.billing_requisites for each row execute function update_updated_at();


-- public.payment_provider_accounts определение

-- Drop table

-- DROP TABLE public.payment_provider_accounts;

CREATE TABLE public.payment_provider_accounts (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	provider_code public."payment_provider_code" NOT NULL,
	title varchar NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	is_test_mode bool DEFAULT false NOT NULL,
	auth_type public."payment_account_auth_type" NOT NULL,
	merchant_id varchar NULL,
	terminal_key varchar NULL,
	shop_code varchar NULL,
	customer_code varchar NULL,
	client_id varchar NULL,
	client_secret_encrypted text NULL,
	api_token_encrypted text NULL,
	refresh_token_encrypted text NULL,
	public_key text NULL,
	private_key_encrypted text NULL,
	webhook_url varchar NULL,
	success_url varchar NULL,
	fail_url varchar NULL,
	metadata jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT payment_provider_accounts_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_payment_provider_accounts_is_active ON public.payment_provider_accounts USING btree (is_active);
CREATE INDEX idx_payment_provider_accounts_provider_code ON public.payment_provider_accounts USING btree (provider_code);

-- Table Triggers

create trigger trg_payment_provider_accounts_update_updated_at before
update
    on
    public.payment_provider_accounts for each row execute function update_updated_at();


-- public.platforms определение

-- Drop table

-- DROP TABLE public.platforms;

CREATE TABLE public.platforms (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code varchar NOT NULL,
	"name" varchar NOT NULL,
	description text NULL,
	website_url varchar NULL,
	icon_url varchar NULL,
	is_active bool DEFAULT true NOT NULL,
	is_in_testing bool DEFAULT false NOT NULL,
	can_be_source bool DEFAULT true NOT NULL,
	can_be_delivery bool DEFAULT true NOT NULL,
	can_be_feedback_source bool DEFAULT true NOT NULL,
	metadata jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT platforms_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_platforms_is_active ON public.platforms USING btree (is_active);
CREATE INDEX idx_platforms_is_in_testing ON public.platforms USING btree (is_in_testing);
CREATE UNIQUE INDEX uq_platforms_code_ci ON public.platforms USING btree (lower((code)::text));

-- Table Triggers

create trigger trg_platforms_update_updated_at before
update
    on
    public.platforms for each row execute function update_updated_at();


-- public.resource_op_types определение

-- Drop table

-- DROP TABLE public.resource_op_types;

CREATE TABLE public.resource_op_types (
	id int4 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code text NOT NULL,
	"name" text NULL,
	rph_limit int4 NOT NULL,
	reserve_percent numeric DEFAULT 10 NOT NULL,
	is_enabled bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT resource_op_types_code_key UNIQUE (code),
	CONSTRAINT resource_op_types_pkey PRIMARY KEY (id)
);

-- Table Triggers

create trigger trg_resource_op_types_update_updated_at before
update
    on
    public.resource_op_types for each row execute function update_updated_at();


-- public.roles определение

-- Drop table

-- DROP TABLE public.roles;

CREATE TABLE public.roles (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code varchar NOT NULL,
	"name" varchar NOT NULL,
	description text NULL,
	is_active bool DEFAULT true NOT NULL,
	is_system bool DEFAULT false NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT roles_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_roles_is_active ON public.roles USING btree (is_active);
CREATE UNIQUE INDEX uq_roles_code_ci ON public.roles USING btree (lower((code)::text));

-- Table Triggers

create trigger trg_roles_update_updated_at before
update
    on
    public.roles for each row execute function update_updated_at();


-- public.task_types определение

-- Drop table

-- DROP TABLE public.task_types;

CREATE TABLE public.task_types (
	id int4 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code text NOT NULL,
	"name" text NOT NULL,
	description text NULL,
	is_enabled bool DEFAULT false NOT NULL,
	default_priority int4 NOT NULL,
	min_available_resource_percent int4 NOT NULL,
	requires_specific_account bool DEFAULT false NOT NULL,
	uses_two_accounts bool DEFAULT false NOT NULL,
	max_attempts int4 DEFAULT 5 NOT NULL,
	retry_delay_seconds int4 DEFAULT 10 NOT NULL,
	retry_backoff_multiplier numeric DEFAULT 2 NOT NULL,
	max_retry_delay_seconds int4 DEFAULT 1800 NOT NULL,
	target_queue_size int4 NULL,
	max_postpone_count int4 DEFAULT 100 NOT NULL,
	task_timeout_seconds int4 DEFAULT 300 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT task_types_code_key UNIQUE (code),
	CONSTRAINT task_types_pkey PRIMARY KEY (id)
);

-- Table Triggers

create trigger trg_task_types_update_updated_at before
update
    on
    public.task_types for each row execute function update_updated_at();


-- public.users определение

-- Drop table

-- DROP TABLE public.users;

CREATE TABLE public.users (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	"name" varchar NOT NULL,
	email varchar NOT NULL,
	password_hash text NULL,
	email_verified bool DEFAULT false NOT NULL,
	status public."user_status" DEFAULT 'active'::user_status NOT NULL,
	blocked_at timestamptz NULL,
	last_login_at timestamptz NULL,
	trial_used bool DEFAULT false NOT NULL,
	timezone varchar NULL,
	locale varchar NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	deleted_at timestamptz NULL,
	avatar_url varchar NULL,
	CONSTRAINT users_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_users_status ON public.users USING btree (status);
CREATE UNIQUE INDEX uq_users_email_ci ON public.users USING btree (lower((email)::text));

-- Table Triggers

create trigger trg_users_update_updated_at before
update
    on
    public.users for each row execute function update_updated_at();


-- public.webhook_outbox определение

-- Drop table

-- DROP TABLE public.webhook_outbox;

CREATE TABLE public.webhook_outbox (
	id bigserial NOT NULL,
	"source" text NOT NULL,
	"event" text NOT NULL,
	payload jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	sent_at timestamptz NULL,
	attempts int4 DEFAULT 0 NOT NULL,
	last_error text NULL,
	CONSTRAINT webhook_outbox_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_webhook_outbox_pending ON public.webhook_outbox USING btree (created_at) WHERE (sent_at IS NULL);


-- public.contact_channel_bindings определение

-- Drop table

-- DROP TABLE public.contact_channel_bindings;

CREATE TABLE public.contact_channel_bindings (
	id bigserial NOT NULL,
	user_id int8 NOT NULL,
	platform_id int8 NOT NULL,
	token_hash varchar NOT NULL,
	status public."binding_status" DEFAULT 'pending'::binding_status NOT NULL,
	expires_at timestamptz NOT NULL,
	used_at timestamptz NULL,
	used_external_recipient_id varchar NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT contact_channel_bindings_pkey PRIMARY KEY (id),
	CONSTRAINT fk_contact_channel_bindings_platform FOREIGN KEY (platform_id) REFERENCES public.platforms(id) ON DELETE RESTRICT,
	CONSTRAINT fk_contact_channel_bindings_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);
CREATE INDEX idx_contact_channel_bindings_status_expires_at ON public.contact_channel_bindings USING btree (status, expires_at);
CREATE INDEX idx_contact_channel_bindings_user_id ON public.contact_channel_bindings USING btree (user_id);
CREATE UNIQUE INDEX uq_contact_channel_bindings_token_hash ON public.contact_channel_bindings USING btree (token_hash);


-- public.platform_access_roles определение

-- Drop table

-- DROP TABLE public.platform_access_roles;

CREATE TABLE public.platform_access_roles (
	platform_id int8 NOT NULL,
	role_id int8 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT platform_access_roles_pkey PRIMARY KEY (platform_id, role_id),
	CONSTRAINT fk_platform_access_roles_platform FOREIGN KEY (platform_id) REFERENCES public.platforms(id) ON DELETE CASCADE,
	CONSTRAINT fk_platform_access_roles_role FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT
);
CREATE INDEX idx_platform_access_roles_role_id ON public.platform_access_roles USING btree (role_id);


-- public.subscription_plans определение

-- Drop table

-- DROP TABLE public.subscription_plans;

CREATE TABLE public.subscription_plans (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code varchar NOT NULL,
	"name" varchar NOT NULL,
	description text NULL,
	price_amount numeric(12, 2) NOT NULL,
	price_currency bpchar(3) NOT NULL,
	"billing_period" public."billing_period" NOT NULL,
	"scope" public."plan_scope" DEFAULT 'public'::plan_scope NOT NULL,
	owner_user_id int8 NULL,
	is_active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	archived_at timestamptz NULL,
	max_channels int4 DEFAULT 0 NOT NULL,
	max_messages_per_period int8 DEFAULT 0 NOT NULL,
	CONSTRAINT chk_subscription_plans_price CHECK ((price_amount >= (0)::numeric)),
	CONSTRAINT chk_subscription_plans_scope_owner CHECK ((((scope = 'custom'::plan_scope) AND (owner_user_id IS NOT NULL)) OR ((scope = 'public'::plan_scope) AND (owner_user_id IS NULL)))),
	CONSTRAINT subscription_plans_pkey PRIMARY KEY (id),
	CONSTRAINT fk_subscription_plans_owner_user FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE SET NULL
);
CREATE INDEX idx_subscription_plans_is_active ON public.subscription_plans USING btree (is_active);
CREATE INDEX idx_subscription_plans_owner_user_id ON public.subscription_plans USING btree (owner_user_id);

-- Table Triggers

create trigger trg_subscription_plans_update_updated_at before
update
    on
    public.subscription_plans for each row execute function update_updated_at();


-- public.task_type_ops определение

-- Drop table

-- DROP TABLE public.task_type_ops;

CREATE TABLE public.task_type_ops (
	id int4 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
	task_type_id int4 NOT NULL,
	op_type_id int4 NOT NULL,
	units_per_execution int4 DEFAULT 1 NOT NULL,
	account_role public."task_op_account_role" DEFAULT 'primary'::task_op_account_role NOT NULL,
	CONSTRAINT task_type_ops_pkey PRIMARY KEY (id),
	CONSTRAINT task_type_ops_op_type_id_fkey FOREIGN KEY (op_type_id) REFERENCES public.resource_op_types(id) DEFERRABLE,
	CONSTRAINT task_type_ops_task_type_id_fkey FOREIGN KEY (task_type_id) REFERENCES public.task_types(id) DEFERRABLE
);
CREATE UNIQUE INDEX idx_task_type_ops_unique ON public.task_type_ops USING btree (task_type_id, op_type_id, account_role);


-- public.user_contact_channels определение

-- Drop table

-- DROP TABLE public.user_contact_channels;

CREATE TABLE public.user_contact_channels (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	user_id int8 NOT NULL,
	platform_id int8 NOT NULL,
	external_recipient_id varchar NOT NULL,
	handle varchar NULL,
	display_name varchar NULL,
	profile_url varchar NULL,
	metadata jsonb NULL,
	is_primary bool DEFAULT false NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	verified_at timestamptz(6) NULL,
	CONSTRAINT uq_user_contact_channels_platform_external UNIQUE (platform_id, external_recipient_id),
	CONSTRAINT uq_user_contact_channels_user_id_id UNIQUE (user_id, id),
	CONSTRAINT user_contact_channels_pkey PRIMARY KEY (id),
	CONSTRAINT fk_user_contact_channels_platform FOREIGN KEY (platform_id) REFERENCES public.platforms(id) ON DELETE RESTRICT,
	CONSTRAINT fk_user_contact_channels_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);
CREATE INDEX idx_user_contact_channels_platform_id ON public.user_contact_channels USING btree (platform_id);
CREATE INDEX idx_user_contact_channels_user_id ON public.user_contact_channels USING btree (user_id);
CREATE UNIQUE INDEX uq_user_contact_channels_one_primary ON public.user_contact_channels USING btree (user_id) WHERE ((is_primary = true) AND (is_active = true));
CREATE UNIQUE INDEX uq_user_contact_channels_user_platform_active ON public.user_contact_channels USING btree (user_id, platform_id) WHERE (is_active = true);

-- Table Triggers

create trigger trg_user_contact_channels_update_updated_at before
update
    on
    public.user_contact_channels for each row execute function update_updated_at();


-- public.user_notification_channels определение

-- Drop table

-- DROP TABLE public.user_notification_channels;

CREATE TABLE public.user_notification_channels (
	id bigserial NOT NULL,
	user_id int8 NOT NULL,
	channel varchar(16) NOT NULL,
	username varchar(255) NULL,
	chat_id varchar(255) NULL,
	clear_to_send bool DEFAULT false NOT NULL,
	status public."user_notification_channel_status" DEFAULT 'pending'::user_notification_channel_status NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT chk_unc_channel CHECK (((channel)::text = ANY (ARRAY[('telegram'::character varying)::text, ('vk'::character varying)::text]))),
	CONSTRAINT chk_unc_send_only_active CHECK (((NOT clear_to_send) OR (status = 'active'::user_notification_channel_status))),
	CONSTRAINT chk_unc_target_exists CHECK ((((username IS NOT NULL) AND (btrim((username)::text) <> ''::text)) OR ((chat_id IS NOT NULL) AND (btrim((chat_id)::text) <> ''::text)))),
	CONSTRAINT chk_unc_telegram_requires_chat CHECK ((((channel)::text <> 'telegram'::text) OR ((chat_id IS NOT NULL) AND (btrim((chat_id)::text) <> ''::text)))),
	CONSTRAINT user_notification_channels_pkey PRIMARY KEY (id),
	CONSTRAINT fk_unc_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);
CREATE INDEX idx_unc_ready_to_send ON public.user_notification_channels USING btree (user_id) WHERE ((status = 'active'::user_notification_channel_status) AND (clear_to_send = true));
CREATE INDEX idx_unc_user_status_send ON public.user_notification_channels USING btree (user_id, status, clear_to_send);
CREATE UNIQUE INDEX uq_unc_user_channel_chat ON public.user_notification_channels USING btree (user_id, channel, chat_id) WHERE (chat_id IS NOT NULL);
CREATE UNIQUE INDEX uq_unc_user_channel_username_ci ON public.user_notification_channels USING btree (user_id, channel, lower((username)::text)) WHERE (username IS NOT NULL);

-- Table Triggers

create trigger trg_user_notification_channels_update_updated_at before
update
    on
    public.user_notification_channels for each row execute function update_updated_at();


-- public.user_roles определение

-- Drop table

-- DROP TABLE public.user_roles;

CREATE TABLE public.user_roles (
	user_id int8 NOT NULL,
	role_id int8 NOT NULL,
	assigned_by_user_id int8 NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id),
	CONSTRAINT fk_user_roles_assigned_by_user FOREIGN KEY (assigned_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT fk_user_roles_role FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT,
	CONSTRAINT fk_user_roles_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);


-- public.user_wallets определение

-- Drop table

-- DROP TABLE public.user_wallets;

CREATE TABLE public.user_wallets (
	id bigserial NOT NULL,
	user_id int8 NOT NULL,
	balance numeric(12, 2) DEFAULT 0 NOT NULL,
	currency bpchar(3) DEFAULT 'RUB'::bpchar NOT NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	saved_card_pan varchar NULL,
	saved_card_type varchar NULL,
	tinkoff_customer_key varchar NULL,
	tinkoff_rebill_id varchar NULL,
	CONSTRAINT user_wallets_pkey PRIMARY KEY (id),
	CONSTRAINT fk_user_wallets_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX user_wallets_user_id_key ON public.user_wallets USING btree (user_id);


-- public.verification_tokens определение

-- Drop table

-- DROP TABLE public.verification_tokens;

CREATE TABLE public.verification_tokens (
	id bigserial NOT NULL,
	user_id int8 NOT NULL,
	"token" varchar(64) NOT NULL,
	expires_at timestamptz(6) NOT NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	"type" public."verification_token_type" DEFAULT 'email_verification'::verification_token_type NOT NULL,
	CONSTRAINT verification_tokens_pkey PRIMARY KEY (id),
	CONSTRAINT fk_verification_tokens_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);
CREATE INDEX idx_verification_tokens_user_id ON public.verification_tokens USING btree (user_id);
CREATE UNIQUE INDEX verification_tokens_token_key ON public.verification_tokens USING btree (token);


-- public.promo_codes определение

-- Drop table

-- DROP TABLE public.promo_codes;

CREATE TABLE public.promo_codes (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	code varchar NOT NULL,
	description text NULL,
	"type" public."promo_code_type" NOT NULL,
	discount_percent int4 NULL,
	discount_amount int4 NULL,
	plan_id int8 NULL,
	plan_months int4 DEFAULT 1 NOT NULL,
	is_single_use bool DEFAULT true NOT NULL,
	max_uses int4 NULL,
	used_count int4 DEFAULT 0 NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	expires_at timestamptz NULL,
	created_by_id int8 NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT chk_promo_codes_discount_amount CHECK (((discount_amount IS NULL) OR (discount_amount > 0))),
	CONSTRAINT chk_promo_codes_discount_percent CHECK (((discount_percent IS NULL) OR ((discount_percent > 0) AND (discount_percent <= 100)))),
	CONSTRAINT chk_promo_codes_plan_months CHECK ((plan_months > 0)),
	CONSTRAINT promo_codes_pkey PRIMARY KEY (id),
	CONSTRAINT uq_promo_codes_code UNIQUE (code),
	CONSTRAINT promo_codes_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id) ON DELETE SET NULL,
	CONSTRAINT promo_codes_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.subscription_plans(id) ON DELETE SET NULL
);
CREATE INDEX idx_promo_codes_active_expires ON public.promo_codes USING btree (is_active, expires_at);


-- public.account_resource_usage определение

-- Drop table

-- DROP TABLE public.account_resource_usage;

CREATE TABLE public.account_resource_usage (
	id int8 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	account_id int8 NOT NULL,
	op_type_id int4 NOT NULL,
	task_id int8 NOT NULL,
	task_attempt_id int8 NULL,
	task_type_id int4 NOT NULL,
	units int4 DEFAULT 1 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT account_resource_usage_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_account_resource_usage_account_hour ON public.account_resource_usage USING btree (account_id, created_at DESC);
CREATE INDEX idx_account_resource_usage_account_op_hour ON public.account_resource_usage USING btree (account_id, op_type_id, created_at DESC);
CREATE INDEX idx_account_resource_usage_attempt ON public.account_resource_usage USING btree (task_attempt_id) WHERE (task_attempt_id IS NOT NULL);
CREATE INDEX idx_account_resource_usage_task ON public.account_resource_usage USING btree (task_id);


-- public.accounts определение

-- Drop table

-- DROP TABLE public.accounts;

CREATE TABLE public.accounts (
	id int8 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	telegram_account_id text NULL,
	session_name text NOT NULL,
	status public."account_status" DEFAULT 'active'::account_status NOT NULL,
	is_enabled bool DEFAULT true NOT NULL,
	cooldown_until timestamptz NULL,
	current_task_id int8 NULL,
	last_used_at timestamptz NULL,
	last_error text NULL,
	last_error_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT accounts_pkey PRIMARY KEY (id),
	CONSTRAINT accounts_session_name_key UNIQUE (session_name)
);
CREATE INDEX idx_accounts_pick_available ON public.accounts USING btree (cooldown_until, last_used_at) WHERE ((status = 'active'::account_status) AND (is_enabled = true) AND (current_task_id IS NULL));
CREATE INDEX idx_accounts_status_cooldown ON public.accounts USING btree (status, cooldown_until) WHERE (status = ANY (ARRAY['active'::account_status, 'cooldown'::account_status]));

-- Table Triggers

create trigger trg_accounts_update_updated_at before
update
    on
    public.accounts for each row execute function update_updated_at();


-- public.analytics_daily_snapshots определение

-- Drop table

-- DROP TABLE public.analytics_daily_snapshots;

CREATE TABLE public.analytics_daily_snapshots (
	id bigserial NOT NULL,
	monitoring_project_id int8 NOT NULL,
	snapshot_date date NOT NULL,
	messages_ingested int4 DEFAULT 0 NOT NULL,
	messages_with_lead_signal int4 DEFAULT 0 NOT NULL,
	leads_found int4 DEFAULT 0 NOT NULL,
	leads_direct int4 DEFAULT 0 NOT NULL,
	leads_indirect int4 DEFAULT 0 NOT NULL,
	leads_confirmed int4 DEFAULT 0 NOT NULL,
	leads_rejected int4 DEFAULT 0 NOT NULL,
	by_platform jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT analytics_daily_snapshots_pkey PRIMARY KEY (id),
	CONSTRAINT uq_analytics_daily_snapshots_project_date UNIQUE (monitoring_project_id, snapshot_date)
);
CREATE INDEX idx_analytics_daily_snapshots_project_date ON public.analytics_daily_snapshots USING btree (monitoring_project_id, snapshot_date DESC);


-- public.audit_logs определение

-- Drop table

-- DROP TABLE public.audit_logs;

CREATE TABLE public.audit_logs (
	id bigserial NOT NULL,
	user_id int8 NULL,
	project_id int8 NULL,
	"action" varchar(100) NOT NULL,
	entity_type varchar(50) NULL,
	entity_id varchar(100) NULL,
	meta jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT audit_logs_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_audit_logs_created_at ON public.audit_logs USING btree (created_at);
CREATE INDEX idx_audit_logs_project_id ON public.audit_logs USING btree (project_id);
CREATE INDEX idx_audit_logs_user_id ON public.audit_logs USING btree (user_id);


-- public.billing_invoices определение

-- Drop table

-- DROP TABLE public.billing_invoices;

CREATE TABLE public.billing_invoices (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	user_id int8 NOT NULL,
	subscription_plan_id int8 NULL,
	provider_account_id int8 NULL,
	billing_requisites_id int8 NULL,
	invoice_no varchar NOT NULL,
	provider_invoice_id varchar NULL,
	provider_order_id varchar NULL,
	status public."billing_invoice_status" DEFAULT 'draft'::billing_invoice_status NOT NULL,
	amount numeric(12, 2) NOT NULL,
	currency bpchar(3) NOT NULL,
	description text NULL,
	payment_url varchar NULL,
	expires_at timestamptz NULL,
	paid_at timestamptz NULL,
	canceled_at timestamptz NULL,
	provider_status varchar NULL,
	raw_provider_payload jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	project_subscription_id int8 NULL,
	CONSTRAINT billing_invoices_pkey PRIMARY KEY (id),
	CONSTRAINT chk_billing_invoices_amount CHECK ((amount >= (0)::numeric)),
	CONSTRAINT uq_billing_invoices_invoice_no UNIQUE (invoice_no)
);
CREATE INDEX idx_billing_invoices_provider_account_id ON public.billing_invoices USING btree (provider_account_id);
CREATE INDEX idx_billing_invoices_user_status_created ON public.billing_invoices USING btree (user_id, status, created_at);
CREATE UNIQUE INDEX uq_billing_invoices_provider_invoice ON public.billing_invoices USING btree (provider_account_id, provider_invoice_id) WHERE (provider_invoice_id IS NOT NULL);
CREATE UNIQUE INDEX uq_billing_invoices_provider_order ON public.billing_invoices USING btree (provider_account_id, provider_order_id) WHERE (provider_order_id IS NOT NULL);

-- Table Triggers

create trigger trg_billing_invoices_update_updated_at before
update
    on
    public.billing_invoices for each row execute function update_updated_at();


-- public.billing_payments определение

-- Drop table

-- DROP TABLE public.billing_payments;

CREATE TABLE public.billing_payments (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	billing_invoice_id int8 NOT NULL,
	provider_account_id int8 NULL,
	provider_payment_id varchar NULL,
	provider_operation_id varchar NULL,
	status public."billing_payment_status" DEFAULT 'created'::billing_payment_status NOT NULL,
	amount numeric(12, 2) NOT NULL,
	currency bpchar(3) NOT NULL,
	paid_amount numeric(12, 2) NULL,
	commission_amount numeric(12, 2) NULL,
	paid_at timestamptz NULL,
	confirmed_at timestamptz NULL,
	refunded_at timestamptz NULL,
	verification_source varchar NULL,
	raw_provider_payload jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT billing_payments_pkey PRIMARY KEY (id),
	CONSTRAINT chk_billing_payments_amount CHECK ((amount >= (0)::numeric)),
	CONSTRAINT chk_billing_payments_commission_amount CHECK (((commission_amount IS NULL) OR (commission_amount >= (0)::numeric))),
	CONSTRAINT chk_billing_payments_paid_amount CHECK (((paid_amount IS NULL) OR (paid_amount >= (0)::numeric)))
);
CREATE INDEX idx_billing_payments_invoice_status ON public.billing_payments USING btree (billing_invoice_id, status);
CREATE INDEX idx_billing_payments_provider_account_id ON public.billing_payments USING btree (provider_account_id);
CREATE UNIQUE INDEX uq_billing_payments_provider_operation ON public.billing_payments USING btree (provider_account_id, provider_operation_id) WHERE (provider_operation_id IS NOT NULL);
CREATE UNIQUE INDEX uq_billing_payments_provider_payment ON public.billing_payments USING btree (provider_account_id, provider_payment_id) WHERE (provider_payment_id IS NOT NULL);

-- Table Triggers

create trigger trg_billing_payments_update_updated_at before
update
    on
    public.billing_payments for each row execute function update_updated_at();


-- public.billing_webhook_events определение

-- Drop table

-- DROP TABLE public.billing_webhook_events;

CREATE TABLE public.billing_webhook_events (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	provider_account_id int8 NOT NULL,
	billing_invoice_id int8 NULL,
	billing_payment_id int8 NULL,
	provider_event_id varchar NULL,
	event_type varchar NOT NULL,
	signature_valid bool DEFAULT false NOT NULL,
	process_status public."webhook_process_status" DEFAULT 'new'::webhook_process_status NOT NULL,
	payload jsonb NOT NULL,
	error_text text NULL,
	received_at timestamptz DEFAULT now() NOT NULL,
	processed_at timestamptz NULL,
	CONSTRAINT billing_webhook_events_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_billing_webhook_events_process_status ON public.billing_webhook_events USING btree (process_status);
CREATE INDEX idx_billing_webhook_events_provider_account_id ON public.billing_webhook_events USING btree (provider_account_id);
CREATE UNIQUE INDEX uq_billing_webhook_events_provider_event ON public.billing_webhook_events USING btree (provider_account_id, provider_event_id) WHERE (provider_event_id IS NOT NULL);


-- public.in_app_notifications определение

-- Drop table

-- DROP TABLE public.in_app_notifications;

CREATE TABLE public.in_app_notifications (
	id bigserial NOT NULL,
	"type" public."notification_type" NOT NULL,
	title varchar NOT NULL,
	body text NULL,
	entity_type varchar NULL,
	entity_id int8 NULL,
	metadata jsonb NULL,
	is_read bool DEFAULT false NOT NULL,
	read_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	project_id int8 NOT NULL,
	CONSTRAINT in_app_notifications_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_in_app_notifications_entity ON public.in_app_notifications USING btree (entity_type, entity_id);
CREATE INDEX idx_in_app_notifications_project_created_at ON public.in_app_notifications USING btree (project_id, created_at DESC);
CREATE INDEX idx_in_app_notifications_project_is_read ON public.in_app_notifications USING btree (project_id, is_read);


-- public.message_ai_screening_runs определение

-- Drop table

-- DROP TABLE public.message_ai_screening_runs;

CREATE TABLE public.message_ai_screening_runs (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	monitoring_project_id int8 NOT NULL,
	source_message_id int8 NOT NULL,
	run_status public."screening_run_status" DEFAULT 'queued'::screening_run_status NOT NULL,
	ai_model varchar NULL,
	prompt_version varchar NULL,
	pipeline_version varchar NULL,
	first_level_filter_data jsonb NULL,
	input_message_text text NULL,
	ai_result_text text NULL,
	ai_result_payload jsonb NULL,
	is_match bool DEFAULT false NOT NULL,
	matched_user_id int8 NULL,
	matched_contact_channel_id int8 NULL,
	match_confidence numeric(5, 4) NULL,
	match_reason_short text NULL,
	"delivery_status" public."delivery_status" DEFAULT 'pending'::delivery_status NOT NULL,
	delivery_platform_id int8 NULL,
	delivery_external_id varchar NULL,
	delivered_at timestamptz NULL,
	delivery_error_text text NULL,
	delivery_result_payload jsonb NULL,
	"client_feedback_status" public."client_feedback_status" DEFAULT 'unknown'::client_feedback_status NOT NULL,
	client_feedback_at timestamptz NULL,
	client_feedback_source_platform_id int8 NULL,
	client_feedback_comment text NULL,
	raw_message_payload_snapshot jsonb NULL,
	sender_payload_snapshot jsonb NULL,
	error_text text NULL,
	processed_at timestamptz NULL,
	is_latest bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT chk_message_ai_screening_runs_confidence CHECK (((match_confidence IS NULL) OR ((match_confidence >= (0)::numeric) AND (match_confidence <= (1)::numeric)))),
	CONSTRAINT chk_message_ai_screening_runs_contact_requires_user CHECK (((matched_contact_channel_id IS NULL) OR (matched_user_id IS NOT NULL))),
	CONSTRAINT chk_message_ai_screening_runs_delivery_sent CHECK (((delivered_at IS NULL) OR (delivery_status = 'sent'::delivery_status))),
	CONSTRAINT chk_message_ai_screening_runs_feedback_known CHECK (((client_feedback_at IS NULL) OR (client_feedback_status <> 'unknown'::client_feedback_status))),
	CONSTRAINT message_ai_screening_runs_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_message_ai_screening_runs_matched_contact_channel_id ON public.message_ai_screening_runs USING btree (matched_contact_channel_id);
CREATE INDEX idx_message_ai_screening_runs_matched_user_delivery_created ON public.message_ai_screening_runs USING btree (matched_user_id, delivery_status, created_at);
CREATE INDEX idx_message_ai_screening_runs_matched_user_id ON public.message_ai_screening_runs USING btree (matched_user_id);
CREATE INDEX idx_message_ai_screening_runs_monitoring_project_id ON public.message_ai_screening_runs USING btree (monitoring_project_id);
CREATE INDEX idx_message_ai_screening_runs_project_run_status ON public.message_ai_screening_runs USING btree (monitoring_project_id, run_status);
CREATE INDEX idx_message_ai_screening_runs_run_status ON public.message_ai_screening_runs USING btree (run_status);
CREATE INDEX idx_message_ai_screening_runs_source_message_id ON public.message_ai_screening_runs USING btree (source_message_id);
CREATE UNIQUE INDEX uq_message_ai_screening_runs_latest ON public.message_ai_screening_runs USING btree (monitoring_project_id, source_message_id) WHERE (is_latest = true);

-- Table Triggers

create trigger trg_message_ai_screening_runs_repoint_latest before
insert
    or
update
    of is_latest,
    monitoring_project_id,
    source_message_id on
    public.message_ai_screening_runs for each row execute function repoint_is_latest();
create trigger trg_message_ai_screening_runs_update_updated_at before
update
    on
    public.message_ai_screening_runs for each row execute function update_updated_at();
create trigger trg_outbox_message_ai_screening_runs_insert after
insert
    on
    public.message_ai_screening_runs for each row execute function fn_outbox_on_insert();


-- public.monitoring_projects определение

-- Drop table

-- DROP TABLE public.monitoring_projects;

CREATE TABLE public.monitoring_projects (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	owner_user_id int8 NOT NULL,
	"name" varchar NOT NULL,
	description text NULL,
	status public."project_status" DEFAULT 'active'::project_status NOT NULL,
	lead_search_prompt text NOT NULL,
	lead_search_prompt_version int4 DEFAULT 1 NOT NULL,
	outreach_message_template text NULL,
	outreach_message_template_version int4 DEFAULT 1 NOT NULL,
	filters_version int4 DEFAULT 1 NOT NULL,
	search_filters jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	archived_at timestamptz NULL,
	deleted_at timestamptz NULL,
	last_message_id int8 NULL,
	score_filters jsonb NULL,
	CONSTRAINT chk_monitoring_projects_filters_version CHECK ((filters_version > 0)),
	CONSTRAINT chk_monitoring_projects_prompt_version CHECK ((lead_search_prompt_version > 0)),
	CONSTRAINT chk_monitoring_projects_template_version CHECK ((outreach_message_template_version > 0)),
	CONSTRAINT monitoring_projects_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_monitoring_projects_owner_user_id ON public.monitoring_projects USING btree (owner_user_id);
CREATE INDEX idx_monitoring_projects_owner_user_status ON public.monitoring_projects USING btree (owner_user_id, status);
CREATE INDEX idx_monitoring_projects_status ON public.monitoring_projects USING btree (status);

-- Table Triggers

create trigger trg_increment_lead_search_prompt_version before
update
    on
    public.monitoring_projects for each row execute function increment_lead_search_prompt_version();
create trigger trg_monitoring_projects_update_updated_at before
update
    on
    public.monitoring_projects for each row execute function update_updated_at();


-- public.project_source_channels определение

-- Drop table

-- DROP TABLE public.project_source_channels;

CREATE TABLE public.project_source_channels (
	monitoring_project_id int8 NOT NULL,
	source_channel_id int8 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	is_enabled bool DEFAULT true NOT NULL,
	last_seen_message_id int8 NULL,
	CONSTRAINT project_source_channels_pkey PRIMARY KEY (monitoring_project_id, source_channel_id)
);
CREATE INDEX idx_project_source_channels_source_channel_id ON public.project_source_channels USING btree (source_channel_id);

-- Table Triggers

create trigger trg_project_source_channels_recount after
insert
    or
delete
    or
update
    of source_channel_id on
    public.project_source_channels for each row execute function recount_linked_projects_count();
create trigger trg_project_source_channels_update_updated_at before
update
    on
    public.project_source_channels for each row execute function update_updated_at();
create trigger trg_sync_channel_active after
insert
    or
delete
    or
update
    on
    public.project_source_channels for each row execute function sync_source_channel_active_state();


-- public.project_subscriptions определение

-- Drop table

-- DROP TABLE public.project_subscriptions;

CREATE TABLE public.project_subscriptions (
	id bigserial NOT NULL,
	monitoring_project_id int8 NOT NULL,
	user_id int8 NOT NULL,
	subscription_plan_id int8 NOT NULL,
	status public."subscription_status" DEFAULT 'pending'::subscription_status NOT NULL,
	starts_at timestamptz(6) NOT NULL,
	ends_at timestamptz(6) NULL,
	auto_renew bool DEFAULT false NOT NULL,
	canceled_at timestamptz(6) NULL,
	paused_at timestamptz(6) NULL,
	expired_at timestamptz(6) NULL,
	assigned_reason varchar NULL,
	plan_name_snapshot varchar NOT NULL,
	plan_code_snapshot varchar NOT NULL,
	price_amount_snapshot numeric(12, 2) NOT NULL,
	price_currency_snapshot bpchar(3) NOT NULL,
	billing_period_snapshot public."billing_period" NOT NULL,
	max_messages_snapshot int8 DEFAULT 0 NOT NULL,
	max_channels_snapshot int4 DEFAULT 0 NOT NULL,
	messages_used int8 DEFAULT 0 NOT NULL,
	channels_used int4 DEFAULT 0 NOT NULL,
	current_period_starts_at timestamptz(6) NOT NULL,
	current_period_ends_at timestamptz(6) NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT project_subscriptions_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_project_subscriptions_period_ends ON public.project_subscriptions USING btree (current_period_ends_at);
CREATE INDEX idx_project_subscriptions_plan_id ON public.project_subscriptions USING btree (subscription_plan_id);
CREATE INDEX idx_project_subscriptions_project_id ON public.project_subscriptions USING btree (monitoring_project_id);
CREATE INDEX idx_project_subscriptions_status ON public.project_subscriptions USING btree (status);
CREATE INDEX idx_project_subscriptions_user_id ON public.project_subscriptions USING btree (user_id);


-- public.promo_code_usages определение

-- Drop table

-- DROP TABLE public.promo_code_usages;

CREATE TABLE public.promo_code_usages (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	promo_code_id int8 NOT NULL,
	user_id int8 NOT NULL,
	project_id int8 NOT NULL,
	discount_applied int4 NULL,
	used_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT promo_code_usages_pkey PRIMARY KEY (id),
	CONSTRAINT uq_promo_code_usages_code_project UNIQUE (promo_code_id, project_id),
	CONSTRAINT uq_promo_code_usages_code_user UNIQUE (promo_code_id, user_id)
);
CREATE INDEX idx_promo_code_usages_user ON public.promo_code_usages USING btree (user_id);


-- public.source_channel_requests определение

-- Drop table

-- DROP TABLE public.source_channel_requests;

CREATE TABLE public.source_channel_requests (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	user_id int8 NOT NULL,
	monitoring_project_id int8 NULL,
	platform_id int8 NULL,
	requested_platform_code varchar NULL,
	requested_platform_name varchar NULL,
	requested_channel_name varchar NULL,
	requested_external_id varchar NULL,
	requested_url varchar NULL,
	"comment" text NULL,
	status public."source_channel_request_status" DEFAULT 'new'::source_channel_request_status NOT NULL,
	resolved_by_user_id int8 NULL,
	resolved_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT source_channel_requests_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_source_channel_requests_status ON public.source_channel_requests USING btree (status);
CREATE INDEX idx_source_channel_requests_user_id ON public.source_channel_requests USING btree (user_id);

-- Table Triggers

create trigger trg_source_channel_requests_update_updated_at before
update
    on
    public.source_channel_requests for each row execute function update_updated_at();


-- public.source_channels определение

-- Drop table

-- DROP TABLE public.source_channels;

CREATE TABLE public.source_channels (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	platform_id int8 NOT NULL,
	external_channel_id varchar NOT NULL,
	"name" varchar NULL,
	description text NULL,
	external_url varchar NULL,
	image_url varchar NULL,
	metadata jsonb NULL,
	linked_projects_count int4 DEFAULT 0 NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	assigned_account_id int8 NULL,
	extra_data_collected bool DEFAULT false NOT NULL,
	last_updated_at timestamptz NULL,
	CONSTRAINT chk_source_channels_linked_projects_count CHECK ((linked_projects_count >= 0)),
	CONSTRAINT source_channels_pkey PRIMARY KEY (id),
	CONSTRAINT uq_source_channels_platform_external UNIQUE (platform_id, external_channel_id)
);
CREATE INDEX idx_source_channels_assigned_account ON public.source_channels USING btree (assigned_account_id) WHERE (assigned_account_id IS NOT NULL);
CREATE INDEX idx_source_channels_collect_pending ON public.source_channels USING btree (assigned_account_id, created_at) WHERE ((assigned_account_id IS NOT NULL) AND (extra_data_collected = false));
CREATE INDEX idx_source_channels_linked_projects_count ON public.source_channels USING btree (linked_projects_count);
CREATE INDEX idx_source_channels_platform_id ON public.source_channels USING btree (platform_id);
CREATE INDEX idx_source_channels_stale_update ON public.source_channels USING btree (last_updated_at NULLS FIRST) WHERE (assigned_account_id IS NOT NULL);

-- Table Triggers

create trigger trg_source_channels_update_updated_at before
update
    on
    public.source_channels for each row execute function update_updated_at();


-- public.source_messages определение

-- Drop table

-- DROP TABLE public.source_messages;

CREATE TABLE public.source_messages (
	id int8 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	source_channel_id int8 NOT NULL,
	external_message_id varchar NOT NULL,
	published_at timestamptz NOT NULL,
	ingested_at timestamptz DEFAULT now() NOT NULL,
	edited_at timestamptz NULL,
	deleted_at timestamptz NULL,
	message_text text NULL,
	message_url varchar NULL,
	raw_message_payload jsonb NULL,
	sender_external_id varchar NULL,
	sender_payload jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	filter1_top1_label varchar NULL,
	filter1_top1_score numeric(5, 4) NULL,
	filter1_top2_label varchar NULL,
	filter1_top2_score numeric(5, 4) NULL,
	CONSTRAINT source_messages_pkey PRIMARY KEY (id),
	CONSTRAINT uq_source_messages_channel_external UNIQUE (source_channel_id, external_message_id)
);
CREATE INDEX idx_source_messages_channel_ingested_at ON public.source_messages USING btree (source_channel_id, ingested_at);
CREATE INDEX idx_source_messages_channel_published_at ON public.source_messages USING btree (source_channel_id, published_at);
CREATE INDEX idx_source_messages_filter1_label ON public.source_messages USING btree (filter1_top1_label) WHERE (filter1_top1_label IS NOT NULL);
CREATE INDEX idx_source_messages_published_at ON public.source_messages USING btree (published_at);
CREATE INDEX idx_source_messages_source_channel_id ON public.source_messages USING btree (source_channel_id);

-- Table Triggers

create trigger trg_source_messages_update_updated_at before
update
    on
    public.source_messages for each row execute function update_updated_at();


-- public.task_attempts определение

-- Drop table

-- DROP TABLE public.task_attempts;

CREATE TABLE public.task_attempts (
	id int8 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	task_id int8 NOT NULL,
	task_type_id int4 NOT NULL,
	account_id int8 NOT NULL,
	source_account_id int8 NULL,
	target_account_id int8 NULL,
	attempt_number int4 NOT NULL,
	status public."attempt_status" NOT NULL,
	error_code text NULL,
	error_message text NULL,
	started_at timestamptz NOT NULL,
	finished_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT task_attempts_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_task_attempts_account_errors ON public.task_attempts USING btree (account_id, started_at DESC) WHERE (status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status]));
CREATE UNIQUE INDEX idx_task_attempts_task_number ON public.task_attempts USING btree (task_id, attempt_number);
CREATE INDEX idx_task_attempts_type_errors ON public.task_attempts USING btree (task_type_id, started_at DESC) WHERE (status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status]));


-- public.task_queue определение

-- Drop table

-- DROP TABLE public.task_queue;

CREATE TABLE public.task_queue (
	id int8 GENERATED BY DEFAULT AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START 1 CACHE 1 NO CYCLE) NOT NULL,
	task_type_id int4 NOT NULL,
	task_type_code text NOT NULL,
	status public."task_status" DEFAULT 'queued'::task_status NOT NULL,
	priority int4 NOT NULL,
	channel_id int8 NULL,
	account_id int8 NULL,
	source_account_id int8 NULL,
	target_account_id int8 NULL,
	payload jsonb DEFAULT '{}'::jsonb NOT NULL,
	dedup_key text NULL,
	run_after timestamptz DEFAULT now() NOT NULL,
	locked_by text NULL,
	locked_at timestamptz NULL,
	locked_until timestamptz NULL,
	attempt_count int4 DEFAULT 0 NOT NULL,
	postpone_count int4 DEFAULT 0 NOT NULL,
	max_attempts int4 NOT NULL,
	last_error text NULL,
	last_error_at timestamptz NULL,
	started_at timestamptz NULL,
	finished_at timestamptz NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT task_queue_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_task_queue_account_active ON public.task_queue USING btree (account_id, status) WHERE ((account_id IS NOT NULL) AND (status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status])));
CREATE INDEX idx_task_queue_active_by_type ON public.task_queue USING btree (task_type_id, status) WHERE (status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status]));
CREATE INDEX idx_task_queue_channel_active ON public.task_queue USING btree (channel_id) WHERE ((channel_id IS NOT NULL) AND (status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status])));
CREATE INDEX idx_task_queue_claim_ready ON public.task_queue USING btree (run_after, priority DESC, created_at) WHERE (status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status]));
CREATE UNIQUE INDEX idx_task_queue_dedup_active ON public.task_queue USING btree (dedup_key) WHERE ((dedup_key IS NOT NULL) AND (status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status])));
CREATE INDEX idx_task_queue_dispatch ON public.task_queue USING btree (status, run_after, priority DESC, created_at);
CREATE INDEX idx_task_queue_done_finished ON public.task_queue USING btree (finished_at DESC) WHERE (status = 'done'::task_status);
CREATE INDEX idx_task_queue_high_postpone ON public.task_queue USING btree (postpone_count DESC) WHERE ((status = ANY (ARRAY['scheduled'::task_status, 'retry'::task_status])) AND (postpone_count > 0));
CREATE INDEX idx_task_queue_in_progress_started ON public.task_queue USING btree (started_at) WHERE (status = 'in_progress'::task_status);
CREATE INDEX idx_task_queue_queued_age ON public.task_queue USING btree (created_at) WHERE (status = 'queued'::task_status);
CREATE INDEX idx_task_queue_retry_run_after ON public.task_queue USING btree (run_after) WHERE (status = 'retry'::task_status);
CREATE INDEX idx_task_queue_status_created ON public.task_queue USING btree (status, created_at);
CREATE INDEX idx_task_queue_stuck ON public.task_queue USING btree (status, started_at) WHERE (status = ANY (ARRAY['in_progress'::task_status, 'stuck'::task_status]));

-- Table Triggers

create trigger trg_task_queue_update_updated_at before
update
    on
    public.task_queue for each row execute function update_updated_at();


-- public.wallet_transactions определение

-- Drop table

-- DROP TABLE public.wallet_transactions;

CREATE TABLE public.wallet_transactions (
	id bigserial NOT NULL,
	wallet_id int8 NOT NULL,
	kind varchar NOT NULL,
	amount numeric(12, 2) NOT NULL,
	description varchar NOT NULL,
	monitoring_project_id int8 NULL,
	project_subscription_id int8 NULL,
	status varchar DEFAULT 'pending'::character varying NOT NULL,
	created_at timestamptz(6) DEFAULT CURRENT_TIMESTAMP NOT NULL,
	external_id varchar NULL,
	CONSTRAINT wallet_transactions_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_wallet_transactions_project_id ON public.wallet_transactions USING btree (monitoring_project_id);
CREATE INDEX idx_wallet_transactions_wallet_id ON public.wallet_transactions USING btree (wallet_id);


-- public.account_resource_usage внешние включи

ALTER TABLE public.account_resource_usage ADD CONSTRAINT account_resource_usage_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.account_resource_usage ADD CONSTRAINT account_resource_usage_op_type_id_fkey FOREIGN KEY (op_type_id) REFERENCES public.resource_op_types(id) DEFERRABLE;
ALTER TABLE public.account_resource_usage ADD CONSTRAINT account_resource_usage_task_attempt_id_fkey FOREIGN KEY (task_attempt_id) REFERENCES public.task_attempts(id) DEFERRABLE;
ALTER TABLE public.account_resource_usage ADD CONSTRAINT account_resource_usage_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_queue(id) DEFERRABLE;
ALTER TABLE public.account_resource_usage ADD CONSTRAINT account_resource_usage_task_type_id_fkey FOREIGN KEY (task_type_id) REFERENCES public.task_types(id) DEFERRABLE;


-- public.accounts внешние включи

ALTER TABLE public.accounts ADD CONSTRAINT accounts_current_task_id_fkey FOREIGN KEY (current_task_id) REFERENCES public.task_queue(id) DEFERRABLE;


-- public.analytics_daily_snapshots внешние включи

ALTER TABLE public.analytics_daily_snapshots ADD CONSTRAINT fk_analytics_daily_snapshots_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE CASCADE;


-- public.audit_logs внешние включи

ALTER TABLE public.audit_logs ADD CONSTRAINT audit_logs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.monitoring_projects(id) ON DELETE SET NULL;
ALTER TABLE public.audit_logs ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- public.billing_invoices внешние включи

ALTER TABLE public.billing_invoices ADD CONSTRAINT fk_billing_invoices_billing_requisites FOREIGN KEY (billing_requisites_id) REFERENCES public.billing_requisites(id) ON DELETE SET NULL;
ALTER TABLE public.billing_invoices ADD CONSTRAINT fk_billing_invoices_project_subscription FOREIGN KEY (project_subscription_id) REFERENCES public.project_subscriptions(id) ON DELETE SET NULL;
ALTER TABLE public.billing_invoices ADD CONSTRAINT fk_billing_invoices_provider_account FOREIGN KEY (provider_account_id) REFERENCES public.payment_provider_accounts(id) ON DELETE SET NULL;
ALTER TABLE public.billing_invoices ADD CONSTRAINT fk_billing_invoices_subscription_plan FOREIGN KEY (subscription_plan_id) REFERENCES public.subscription_plans(id) ON DELETE SET NULL;
ALTER TABLE public.billing_invoices ADD CONSTRAINT fk_billing_invoices_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


-- public.billing_payments внешние включи

ALTER TABLE public.billing_payments ADD CONSTRAINT fk_billing_payments_invoice FOREIGN KEY (billing_invoice_id) REFERENCES public.billing_invoices(id) ON DELETE RESTRICT;
ALTER TABLE public.billing_payments ADD CONSTRAINT fk_billing_payments_provider_account FOREIGN KEY (provider_account_id) REFERENCES public.payment_provider_accounts(id) ON DELETE SET NULL;


-- public.billing_webhook_events внешние включи

ALTER TABLE public.billing_webhook_events ADD CONSTRAINT fk_billing_webhook_events_invoice FOREIGN KEY (billing_invoice_id) REFERENCES public.billing_invoices(id) ON DELETE SET NULL;
ALTER TABLE public.billing_webhook_events ADD CONSTRAINT fk_billing_webhook_events_payment FOREIGN KEY (billing_payment_id) REFERENCES public.billing_payments(id) ON DELETE SET NULL;
ALTER TABLE public.billing_webhook_events ADD CONSTRAINT fk_billing_webhook_events_provider_account FOREIGN KEY (provider_account_id) REFERENCES public.payment_provider_accounts(id) ON DELETE RESTRICT;


-- public.in_app_notifications внешние включи

ALTER TABLE public.in_app_notifications ADD CONSTRAINT fk_in_app_notifications_project FOREIGN KEY (project_id) REFERENCES public.monitoring_projects(id) ON DELETE CASCADE;


-- public.message_ai_screening_runs внешние включи

ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_delivery_platform FOREIGN KEY (delivery_platform_id) REFERENCES public.platforms(id) ON DELETE RESTRICT;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_feedback_platform FOREIGN KEY (client_feedback_source_platform_id) REFERENCES public.platforms(id) ON DELETE RESTRICT;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_matched_contact FOREIGN KEY (matched_contact_channel_id) REFERENCES public.user_contact_channels(id) ON DELETE SET NULL;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_matched_user FOREIGN KEY (matched_user_id) REFERENCES public.users(id) ON DELETE SET NULL;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_matched_user_contact_pair FOREIGN KEY (matched_user_id,matched_contact_channel_id) REFERENCES public.user_contact_channels(user_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE RESTRICT;
ALTER TABLE public.message_ai_screening_runs ADD CONSTRAINT fk_message_ai_screening_runs_source_message FOREIGN KEY (source_message_id) REFERENCES public.source_messages(id) ON DELETE RESTRICT;


-- public.monitoring_projects внешние включи

ALTER TABLE public.monitoring_projects ADD CONSTRAINT fk_monitoring_projects_owner_user FOREIGN KEY (owner_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;
ALTER TABLE public.monitoring_projects ADD CONSTRAINT monitoring_projects_last_message_id_fkey FOREIGN KEY (last_message_id) REFERENCES public.source_messages(id) ON DELETE SET NULL;


-- public.project_source_channels внешние включи

ALTER TABLE public.project_source_channels ADD CONSTRAINT fk_project_source_channels_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE CASCADE;
ALTER TABLE public.project_source_channels ADD CONSTRAINT fk_project_source_channels_source_channel FOREIGN KEY (source_channel_id) REFERENCES public.source_channels(id) ON DELETE CASCADE;


-- public.project_subscriptions внешние включи

ALTER TABLE public.project_subscriptions ADD CONSTRAINT fk_project_subscriptions_plan FOREIGN KEY (subscription_plan_id) REFERENCES public.subscription_plans(id) ON DELETE RESTRICT;
ALTER TABLE public.project_subscriptions ADD CONSTRAINT fk_project_subscriptions_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE RESTRICT;
ALTER TABLE public.project_subscriptions ADD CONSTRAINT fk_project_subscriptions_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


-- public.promo_code_usages внешние включи

ALTER TABLE public.promo_code_usages ADD CONSTRAINT promo_code_usages_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.monitoring_projects(id) ON DELETE CASCADE;
ALTER TABLE public.promo_code_usages ADD CONSTRAINT promo_code_usages_promo_code_id_fkey FOREIGN KEY (promo_code_id) REFERENCES public.promo_codes(id) ON DELETE CASCADE;
ALTER TABLE public.promo_code_usages ADD CONSTRAINT promo_code_usages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- public.source_channel_requests внешние включи

ALTER TABLE public.source_channel_requests ADD CONSTRAINT fk_source_channel_requests_platform FOREIGN KEY (platform_id) REFERENCES public.platforms(id) ON DELETE SET NULL;
ALTER TABLE public.source_channel_requests ADD CONSTRAINT fk_source_channel_requests_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE SET NULL;
ALTER TABLE public.source_channel_requests ADD CONSTRAINT fk_source_channel_requests_resolved_by_user FOREIGN KEY (resolved_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;
ALTER TABLE public.source_channel_requests ADD CONSTRAINT fk_source_channel_requests_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


-- public.source_channels внешние включи

ALTER TABLE public.source_channels ADD CONSTRAINT fk_source_channels_assigned_account FOREIGN KEY (assigned_account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.source_channels ADD CONSTRAINT fk_source_channels_platform FOREIGN KEY (platform_id) REFERENCES public.platforms(id) ON DELETE RESTRICT;


-- public.source_messages внешние включи

ALTER TABLE public.source_messages ADD CONSTRAINT fk_source_messages_source_channel FOREIGN KEY (source_channel_id) REFERENCES public.source_channels(id) ON DELETE RESTRICT;


-- public.task_attempts внешние включи

ALTER TABLE public.task_attempts ADD CONSTRAINT task_attempts_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_attempts ADD CONSTRAINT task_attempts_source_account_id_fkey FOREIGN KEY (source_account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_attempts ADD CONSTRAINT task_attempts_target_account_id_fkey FOREIGN KEY (target_account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_attempts ADD CONSTRAINT task_attempts_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_queue(id) DEFERRABLE;
ALTER TABLE public.task_attempts ADD CONSTRAINT task_attempts_task_type_id_fkey FOREIGN KEY (task_type_id) REFERENCES public.task_types(id) DEFERRABLE;


-- public.task_queue внешние включи

ALTER TABLE public.task_queue ADD CONSTRAINT task_queue_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_queue ADD CONSTRAINT task_queue_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.source_channels(id) DEFERRABLE;
ALTER TABLE public.task_queue ADD CONSTRAINT task_queue_source_account_id_fkey FOREIGN KEY (source_account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_queue ADD CONSTRAINT task_queue_target_account_id_fkey FOREIGN KEY (target_account_id) REFERENCES public.accounts(id) DEFERRABLE;
ALTER TABLE public.task_queue ADD CONSTRAINT task_queue_task_type_id_fkey FOREIGN KEY (task_type_id) REFERENCES public.task_types(id) DEFERRABLE;


-- public.wallet_transactions внешние включи

ALTER TABLE public.wallet_transactions ADD CONSTRAINT fk_wallet_transactions_project FOREIGN KEY (monitoring_project_id) REFERENCES public.monitoring_projects(id) ON DELETE SET NULL;
ALTER TABLE public.wallet_transactions ADD CONSTRAINT fk_wallet_transactions_subscription FOREIGN KEY (project_subscription_id) REFERENCES public.project_subscriptions(id) ON DELETE SET NULL;
ALTER TABLE public.wallet_transactions ADD CONSTRAINT fk_wallet_transactions_wallet FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- public.available_platforms_for_user_v исходный текст

CREATE OR REPLACE VIEW public.available_platforms_for_user_v
AS SELECT DISTINCT u.id AS user_id,
    p.id AS platform_id,
    p.code,
    p.name,
    p.is_in_testing,
    p.can_be_source,
    p.can_be_delivery,
    p.can_be_feedback_source
   FROM users u
     JOIN platforms p ON p.is_active = true
     LEFT JOIN user_roles ur ON ur.user_id = u.id
     LEFT JOIN platform_access_roles par ON par.platform_id = p.id AND par.role_id = ur.role_id
  WHERE p.is_in_testing = false OR par.role_id IS NOT NULL;


-- public.latest_screening_runs_v исходный текст

CREATE OR REPLACE VIEW public.latest_screening_runs_v
AS SELECT id,
    monitoring_project_id,
    source_message_id,
    run_status,
    ai_model,
    prompt_version,
    pipeline_version,
    first_level_filter_data,
    input_message_text,
    ai_result_text,
    ai_result_payload,
    is_match,
    matched_user_id,
    matched_contact_channel_id,
    match_confidence,
    match_reason_short,
    delivery_status,
    delivery_platform_id,
    delivery_external_id,
    delivered_at,
    delivery_error_text,
    delivery_result_payload,
    client_feedback_status,
    client_feedback_at,
    client_feedback_source_platform_id,
    client_feedback_comment,
    raw_message_payload_snapshot,
    sender_payload_snapshot,
    error_text,
    processed_at,
    is_latest,
    created_at,
    updated_at
   FROM message_ai_screening_runs
  WHERE is_latest = true;


-- public.users_with_roles_v исходный текст

CREATE OR REPLACE VIEW public.users_with_roles_v
AS SELECT u.id,
    u.name,
    u.email,
    u.status,
    u.email_verified,
    u.trial_used,
    u.created_at,
    u.updated_at,
    array_remove(array_agg(DISTINCT r.code), NULL::character varying) AS role_codes
   FROM users u
     LEFT JOIN user_roles ur ON ur.user_id = u.id
     LEFT JOIN roles r ON r.id = ur.role_id
  GROUP BY u.id, u.name, u.email, u.status, u.email_verified, u.trial_used, u.created_at, u.updated_at;


-- public.v_account_error_rate_last_hour исходный текст

CREATE OR REPLACE VIEW public.v_account_error_rate_last_hour
AS SELECT account_id,
    count(*) AS attempts_last_hour,
    count(*) FILTER (WHERE status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status])) AS errors_last_hour,
        CASE
            WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status]))::numeric / count(*)::numeric * 100::numeric, 2)
            ELSE 0::numeric
        END AS error_rate_percent
   FROM task_attempts
  WHERE started_at >= (now() - '01:00:00'::interval)
  GROUP BY account_id;


-- public.v_account_op_usage_last_hour исходный текст

CREATE OR REPLACE VIEW public.v_account_op_usage_last_hour
AS SELECT a.id AS account_id,
    a.session_name,
    a.status AS account_status,
    rot.id AS op_type_id,
    rot.code AS op_code,
    rot.rph_limit,
    rot.reserve_percent,
    floor(rot.rph_limit::numeric * (1::numeric - rot.reserve_percent / 100.0))::integer AS effective_rph,
    COALESCE(u.used_last_hour, 0::bigint) AS used_last_hour,
    floor(rot.rph_limit::numeric * (1::numeric - rot.reserve_percent / 100.0))::integer - COALESCE(u.used_last_hour, 0::bigint) AS available_resource,
        CASE
            WHEN floor(rot.rph_limit::numeric * (1::numeric - rot.reserve_percent / 100.0)) > 0::numeric THEN round((floor(rot.rph_limit::numeric * (1::numeric - rot.reserve_percent / 100.0)) - COALESCE(u.used_last_hour, 0::bigint)::numeric) / floor(rot.rph_limit::numeric * (1::numeric - rot.reserve_percent / 100.0)) * 100::numeric, 2)
            ELSE 0::numeric
        END AS available_resource_percent
   FROM accounts a
     CROSS JOIN resource_op_types rot
     LEFT JOIN ( SELECT account_resource_usage.account_id,
            account_resource_usage.op_type_id,
            sum(account_resource_usage.units) AS used_last_hour
           FROM account_resource_usage
          WHERE account_resource_usage.created_at >= (now() - '01:00:00'::interval)
          GROUP BY account_resource_usage.account_id, account_resource_usage.op_type_id) u ON u.account_id = a.id AND u.op_type_id = rot.id
  WHERE rot.is_enabled = true;


-- public.v_account_resource_summary исходный текст

CREATE OR REPLACE VIEW public.v_account_resource_summary
AS SELECT account_id,
    session_name,
    account_status,
    min(available_resource_percent) AS worst_available_percent,
    bool_or(available_resource <= 0) AS any_op_exhausted,
    count(*) FILTER (WHERE available_resource <= 0) AS exhausted_ops_count
   FROM v_account_op_usage_last_hour
  GROUP BY account_id, session_name, account_status;


-- public.v_accounts_overview исходный текст

CREATE OR REPLACE VIEW public.v_accounts_overview
AS SELECT count(*) FILTER (WHERE status = 'active'::account_status AND is_enabled = true) AS active_accounts_count,
    count(*) FILTER (WHERE status = 'cooldown'::account_status) AS accounts_in_cooldown,
    count(*) FILTER (WHERE status = 'banned'::account_status) AS banned_accounts_count,
    count(*) FILTER (WHERE status = 'disabled'::account_status) AS disabled_accounts_count,
    count(*) FILTER (WHERE status = 'error'::account_status) AS error_accounts_count,
    ( SELECT count(*) AS count
           FROM v_account_resource_summary
          WHERE v_account_resource_summary.any_op_exhausted = true) AS accounts_without_resource
   FROM accounts;


-- public.v_active_projects_within_limit исходный текст

CREATE OR REPLACE VIEW public.v_active_projects_within_limit
AS SELECT mp.id,
    mp.owner_user_id,
    mp.name,
    mp.description,
    mp.status,
    mp.lead_search_prompt,
    mp.lead_search_prompt_version,
    mp.outreach_message_template,
    mp.outreach_message_template_version,
    mp.filters_version,
    mp.search_filters,
    mp.score_filters,
    mp.created_at,
    mp.updated_at,
    mp.archived_at,
    mp.deleted_at,
    mp.last_message_id
   FROM monitoring_projects mp
     JOIN project_subscriptions ps ON ps.monitoring_project_id = mp.id AND ps.status = 'active'::subscription_status
  WHERE mp.status = 'active'::project_status AND mp.deleted_at IS NULL AND (( SELECT COALESCE(sum(sub.cnt), 0::numeric) AS "coalesce"
           FROM ( SELECT count(sm.id) AS cnt
                   FROM project_source_channels psc
                     JOIN source_messages sm ON sm.source_channel_id = psc.source_channel_id
                  WHERE psc.monitoring_project_id = mp.id AND sm.deleted_at IS NULL AND sm.ingested_at >= GREATEST(psc.created_at, ps.current_period_starts_at)) sub)) < ps.max_messages_snapshot::numeric;


-- public.v_high_postpone_tasks исходный текст

CREATE OR REPLACE VIEW public.v_high_postpone_tasks
AS SELECT id,
    task_type_code,
    status,
    postpone_count,
    last_error,
    run_after,
    created_at
   FROM task_queue
  WHERE (status = ANY (ARRAY['scheduled'::task_status, 'retry'::task_status])) AND postpone_count > 0
  ORDER BY postpone_count DESC;


-- public.v_queue_metrics исходный текст

CREATE OR REPLACE VIEW public.v_queue_metrics
AS SELECT count(*) FILTER (WHERE status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status])) AS queue_size_total,
    count(*) FILTER (WHERE status = 'queued'::task_status) AS queued_count,
    count(*) FILTER (WHERE status = 'scheduled'::task_status) AS scheduled_count,
    count(*) FILTER (WHERE status = 'in_progress'::task_status) AS in_progress_count,
    count(*) FILTER (WHERE status = 'retry'::task_status) AS retry_tasks_count,
    count(*) FILTER (WHERE status = 'stuck'::task_status) AS stuck_tasks_count,
    count(*) FILTER (WHERE status = 'failed'::task_status) AS failed_tasks_count,
    count(*) FILTER (WHERE (status = ANY (ARRAY['scheduled'::task_status, 'retry'::task_status])) AND postpone_count > 0) AS postponed_tasks_count,
    count(*) FILTER (WHERE status = 'done'::task_status AND finished_at >= (now() - '00:05:00'::interval)) AS done_tasks_last_5_min,
    COALESCE(EXTRACT(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued'::task_status))::bigint, 0::bigint) AS oldest_queued_task_age_seconds
   FROM task_queue;


-- public.v_queue_size_by_status исходный текст

CREATE OR REPLACE VIEW public.v_queue_size_by_status
AS SELECT status,
    count(*) AS tasks_count
   FROM task_queue
  GROUP BY status;


-- public.v_queue_size_by_type исходный текст

CREATE OR REPLACE VIEW public.v_queue_size_by_type
AS SELECT task_type_code,
    status,
    count(*) AS tasks_count
   FROM task_queue
  WHERE status = ANY (ARRAY['queued'::task_status, 'scheduled'::task_status, 'retry'::task_status, 'in_progress'::task_status])
  GROUP BY task_type_code, status;


-- public.v_task_type_error_rate_last_hour исходный текст

CREATE OR REPLACE VIEW public.v_task_type_error_rate_last_hour
AS SELECT task_type_id,
    count(*) AS attempts_last_hour,
    count(*) FILTER (WHERE status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status])) AS errors_last_hour,
        CASE
            WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE status = ANY (ARRAY['error'::attempt_status, 'timeout'::attempt_status]))::numeric / count(*)::numeric * 100::numeric, 2)
            ELSE 0::numeric
        END AS error_rate_percent
   FROM task_attempts
  WHERE started_at >= (now() - '01:00:00'::interval)
  GROUP BY task_type_id;



-- DROP FUNCTION public.cash_dist(money, money);

CREATE OR REPLACE FUNCTION public.cash_dist(money, money)
 RETURNS money
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$cash_dist$function$
;

-- DROP FUNCTION public.date_dist(date, date);

CREATE OR REPLACE FUNCTION public.date_dist(date, date)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$date_dist$function$
;

-- DROP FUNCTION public.float4_dist(float4, float4);

CREATE OR REPLACE FUNCTION public.float4_dist(real, real)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$float4_dist$function$
;

-- DROP FUNCTION public.float8_dist(float8, float8);

CREATE OR REPLACE FUNCTION public.float8_dist(double precision, double precision)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$float8_dist$function$
;

-- DROP FUNCTION public.fn_outbox_on_insert();

CREATE OR REPLACE FUNCTION public.fn_outbox_on_insert()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  v_payload JSONB;
BEGIN
  v_payload := jsonb_build_object(
    'event',  'INSERT',
    'schema', TG_TABLE_SCHEMA,
    'table',  TG_TABLE_NAME,
    'row',    row_to_json(NEW),
    'ts',     now()
  );

  INSERT INTO webhook_outbox(source, event, payload)
  VALUES (TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, 'INSERT', v_payload);

  -- NOTIFY для мгновенной реакции воркера; payload намеренно короткий,
  -- т.к. у pg_notify лимит 8000 байт. Сами данные воркер берёт из outbox.
  PERFORM pg_notify('webhook_events', 'new');

  RETURN NEW;
END;
$function$
;

-- DROP FUNCTION public.gbt_bit_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_compress$function$
;

-- DROP FUNCTION public.gbt_bit_consistent(internal, bit, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_consistent(internal, bit, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_consistent$function$
;

-- DROP FUNCTION public.gbt_bit_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_penalty$function$
;

-- DROP FUNCTION public.gbt_bit_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_picksplit$function$
;

-- DROP FUNCTION public.gbt_bit_same(gbtreekey_var, gbtreekey_var, internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_same(gbtreekey_var, gbtreekey_var, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_same$function$
;

-- DROP FUNCTION public.gbt_bit_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_sortsupport$function$
;

-- DROP FUNCTION public.gbt_bit_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bit_union(internal, internal)
 RETURNS gbtreekey_var
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bit_union$function$
;

-- DROP FUNCTION public.gbt_bool_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_compress$function$
;

-- DROP FUNCTION public.gbt_bool_consistent(internal, bool, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_consistent(internal, boolean, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_consistent$function$
;

-- DROP FUNCTION public.gbt_bool_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_fetch$function$
;

-- DROP FUNCTION public.gbt_bool_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_penalty$function$
;

-- DROP FUNCTION public.gbt_bool_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_picksplit$function$
;

-- DROP FUNCTION public.gbt_bool_same(gbtreekey2, gbtreekey2, internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_same(gbtreekey2, gbtreekey2, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_same$function$
;

-- DROP FUNCTION public.gbt_bool_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_sortsupport$function$
;

-- DROP FUNCTION public.gbt_bool_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bool_union(internal, internal)
 RETURNS gbtreekey2
 LANGUAGE c
 IMMUTABLE STRICT
AS '$libdir/btree_gist', $function$gbt_bool_union$function$
;

-- DROP FUNCTION public.gbt_bpchar_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_bpchar_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bpchar_compress$function$
;

-- DROP FUNCTION public.gbt_bpchar_consistent(internal, bpchar, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_bpchar_consistent(internal, character, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bpchar_consistent$function$
;

-- DROP FUNCTION public.gbt_bpchar_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_bpchar_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bpchar_sortsupport$function$
;

-- DROP FUNCTION public.gbt_bytea_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_compress$function$
;

-- DROP FUNCTION public.gbt_bytea_consistent(internal, bytea, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_consistent(internal, bytea, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_consistent$function$
;

-- DROP FUNCTION public.gbt_bytea_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_penalty$function$
;

-- DROP FUNCTION public.gbt_bytea_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_picksplit$function$
;

-- DROP FUNCTION public.gbt_bytea_same(gbtreekey_var, gbtreekey_var, internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_same(gbtreekey_var, gbtreekey_var, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_same$function$
;

-- DROP FUNCTION public.gbt_bytea_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_sortsupport$function$
;

-- DROP FUNCTION public.gbt_bytea_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_bytea_union(internal, internal)
 RETURNS gbtreekey_var
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_bytea_union$function$
;

-- DROP FUNCTION public.gbt_cash_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_compress$function$
;

-- DROP FUNCTION public.gbt_cash_consistent(internal, money, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_consistent(internal, money, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_consistent$function$
;

-- DROP FUNCTION public.gbt_cash_distance(internal, money, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_distance(internal, money, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_distance$function$
;

-- DROP FUNCTION public.gbt_cash_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_fetch$function$
;

-- DROP FUNCTION public.gbt_cash_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_penalty$function$
;

-- DROP FUNCTION public.gbt_cash_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_picksplit$function$
;

-- DROP FUNCTION public.gbt_cash_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_same$function$
;

-- DROP FUNCTION public.gbt_cash_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_sortsupport$function$
;

-- DROP FUNCTION public.gbt_cash_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_cash_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_cash_union$function$
;

-- DROP FUNCTION public.gbt_date_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_date_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_compress$function$
;

-- DROP FUNCTION public.gbt_date_consistent(internal, date, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_consistent(internal, date, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_consistent$function$
;

-- DROP FUNCTION public.gbt_date_distance(internal, date, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_distance(internal, date, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_distance$function$
;

-- DROP FUNCTION public.gbt_date_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_date_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_fetch$function$
;

-- DROP FUNCTION public.gbt_date_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_penalty$function$
;

-- DROP FUNCTION public.gbt_date_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_picksplit$function$
;

-- DROP FUNCTION public.gbt_date_same(gbtreekey8, gbtreekey8, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_same(gbtreekey8, gbtreekey8, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_same$function$
;

-- DROP FUNCTION public.gbt_date_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_date_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_sortsupport$function$
;

-- DROP FUNCTION public.gbt_date_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_date_union(internal, internal)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_date_union$function$
;

-- DROP FUNCTION public.gbt_decompress(internal);

CREATE OR REPLACE FUNCTION public.gbt_decompress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_decompress$function$
;

-- DROP FUNCTION public.gbt_enum_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_compress$function$
;

-- DROP FUNCTION public.gbt_enum_consistent(internal, anyenum, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_consistent(internal, anyenum, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_consistent$function$
;

-- DROP FUNCTION public.gbt_enum_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_fetch$function$
;

-- DROP FUNCTION public.gbt_enum_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_penalty$function$
;

-- DROP FUNCTION public.gbt_enum_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_picksplit$function$
;

-- DROP FUNCTION public.gbt_enum_same(gbtreekey8, gbtreekey8, internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_same(gbtreekey8, gbtreekey8, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_same$function$
;

-- DROP FUNCTION public.gbt_enum_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_sortsupport$function$
;

-- DROP FUNCTION public.gbt_enum_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_enum_union(internal, internal)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_enum_union$function$
;

-- DROP FUNCTION public.gbt_float4_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_compress$function$
;

-- DROP FUNCTION public.gbt_float4_consistent(internal, float4, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_consistent(internal, real, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_consistent$function$
;

-- DROP FUNCTION public.gbt_float4_distance(internal, float4, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_distance(internal, real, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_distance$function$
;

-- DROP FUNCTION public.gbt_float4_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_fetch$function$
;

-- DROP FUNCTION public.gbt_float4_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_penalty$function$
;

-- DROP FUNCTION public.gbt_float4_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_picksplit$function$
;

-- DROP FUNCTION public.gbt_float4_same(gbtreekey8, gbtreekey8, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_same(gbtreekey8, gbtreekey8, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_same$function$
;

-- DROP FUNCTION public.gbt_float4_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_sortsupport$function$
;

-- DROP FUNCTION public.gbt_float4_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float4_union(internal, internal)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float4_union$function$
;

-- DROP FUNCTION public.gbt_float8_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_compress$function$
;

-- DROP FUNCTION public.gbt_float8_consistent(internal, float8, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_consistent(internal, double precision, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_consistent$function$
;

-- DROP FUNCTION public.gbt_float8_distance(internal, float8, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_distance(internal, double precision, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_distance$function$
;

-- DROP FUNCTION public.gbt_float8_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_fetch$function$
;

-- DROP FUNCTION public.gbt_float8_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_penalty$function$
;

-- DROP FUNCTION public.gbt_float8_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_picksplit$function$
;

-- DROP FUNCTION public.gbt_float8_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_same$function$
;

-- DROP FUNCTION public.gbt_float8_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_sortsupport$function$
;

-- DROP FUNCTION public.gbt_float8_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_float8_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_float8_union$function$
;

-- DROP FUNCTION public.gbt_inet_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_compress$function$
;

-- DROP FUNCTION public.gbt_inet_consistent(internal, inet, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_consistent(internal, inet, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_consistent$function$
;

-- DROP FUNCTION public.gbt_inet_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_penalty$function$
;

-- DROP FUNCTION public.gbt_inet_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_picksplit$function$
;

-- DROP FUNCTION public.gbt_inet_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_same$function$
;

-- DROP FUNCTION public.gbt_inet_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_sortsupport$function$
;

-- DROP FUNCTION public.gbt_inet_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_inet_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_inet_union$function$
;

-- DROP FUNCTION public.gbt_int2_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_compress$function$
;

-- DROP FUNCTION public.gbt_int2_consistent(internal, int2, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_consistent(internal, smallint, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_consistent$function$
;

-- DROP FUNCTION public.gbt_int2_distance(internal, int2, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_distance(internal, smallint, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_distance$function$
;

-- DROP FUNCTION public.gbt_int2_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_fetch$function$
;

-- DROP FUNCTION public.gbt_int2_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_penalty$function$
;

-- DROP FUNCTION public.gbt_int2_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_picksplit$function$
;

-- DROP FUNCTION public.gbt_int2_same(gbtreekey4, gbtreekey4, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_same(gbtreekey4, gbtreekey4, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_same$function$
;

-- DROP FUNCTION public.gbt_int2_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_sortsupport$function$
;

-- DROP FUNCTION public.gbt_int2_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int2_union(internal, internal)
 RETURNS gbtreekey4
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int2_union$function$
;

-- DROP FUNCTION public.gbt_int4_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_compress$function$
;

-- DROP FUNCTION public.gbt_int4_consistent(internal, int4, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_consistent(internal, integer, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_consistent$function$
;

-- DROP FUNCTION public.gbt_int4_distance(internal, int4, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_distance(internal, integer, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_distance$function$
;

-- DROP FUNCTION public.gbt_int4_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_fetch$function$
;

-- DROP FUNCTION public.gbt_int4_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_penalty$function$
;

-- DROP FUNCTION public.gbt_int4_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_picksplit$function$
;

-- DROP FUNCTION public.gbt_int4_same(gbtreekey8, gbtreekey8, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_same(gbtreekey8, gbtreekey8, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_same$function$
;

-- DROP FUNCTION public.gbt_int4_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_sortsupport$function$
;

-- DROP FUNCTION public.gbt_int4_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int4_union(internal, internal)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int4_union$function$
;

-- DROP FUNCTION public.gbt_int8_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_compress$function$
;

-- DROP FUNCTION public.gbt_int8_consistent(internal, int8, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_consistent(internal, bigint, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_consistent$function$
;

-- DROP FUNCTION public.gbt_int8_distance(internal, int8, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_distance(internal, bigint, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_distance$function$
;

-- DROP FUNCTION public.gbt_int8_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_fetch$function$
;

-- DROP FUNCTION public.gbt_int8_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_penalty$function$
;

-- DROP FUNCTION public.gbt_int8_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_picksplit$function$
;

-- DROP FUNCTION public.gbt_int8_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_same$function$
;

-- DROP FUNCTION public.gbt_int8_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_sortsupport$function$
;

-- DROP FUNCTION public.gbt_int8_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_int8_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_int8_union$function$
;

-- DROP FUNCTION public.gbt_intv_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_compress$function$
;

-- DROP FUNCTION public.gbt_intv_consistent(internal, interval, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_consistent(internal, interval, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_consistent$function$
;

-- DROP FUNCTION public.gbt_intv_decompress(internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_decompress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_decompress$function$
;

-- DROP FUNCTION public.gbt_intv_distance(internal, interval, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_distance(internal, interval, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_distance$function$
;

-- DROP FUNCTION public.gbt_intv_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_fetch$function$
;

-- DROP FUNCTION public.gbt_intv_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_penalty$function$
;

-- DROP FUNCTION public.gbt_intv_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_picksplit$function$
;

-- DROP FUNCTION public.gbt_intv_same(gbtreekey32, gbtreekey32, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_same(gbtreekey32, gbtreekey32, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_same$function$
;

-- DROP FUNCTION public.gbt_intv_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_sortsupport$function$
;

-- DROP FUNCTION public.gbt_intv_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_intv_union(internal, internal)
 RETURNS gbtreekey32
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_intv_union$function$
;

-- DROP FUNCTION public.gbt_macad8_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_compress$function$
;

-- DROP FUNCTION public.gbt_macad8_consistent(internal, macaddr8, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_consistent(internal, macaddr8, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_consistent$function$
;

-- DROP FUNCTION public.gbt_macad8_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_fetch$function$
;

-- DROP FUNCTION public.gbt_macad8_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_penalty$function$
;

-- DROP FUNCTION public.gbt_macad8_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_picksplit$function$
;

-- DROP FUNCTION public.gbt_macad8_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_same$function$
;

-- DROP FUNCTION public.gbt_macad8_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_sortsupport$function$
;

-- DROP FUNCTION public.gbt_macad8_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad8_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad8_union$function$
;

-- DROP FUNCTION public.gbt_macad_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_compress$function$
;

-- DROP FUNCTION public.gbt_macad_consistent(internal, macaddr, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_consistent(internal, macaddr, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_consistent$function$
;

-- DROP FUNCTION public.gbt_macad_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_fetch$function$
;

-- DROP FUNCTION public.gbt_macad_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_penalty$function$
;

-- DROP FUNCTION public.gbt_macad_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_picksplit$function$
;

-- DROP FUNCTION public.gbt_macad_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_same$function$
;

-- DROP FUNCTION public.gbt_macad_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_macad_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macad_union$function$
;

-- DROP FUNCTION public.gbt_macaddr_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_macaddr_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_macaddr_sortsupport$function$
;

-- DROP FUNCTION public.gbt_numeric_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_compress$function$
;

-- DROP FUNCTION public.gbt_numeric_consistent(internal, numeric, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_consistent(internal, numeric, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_consistent$function$
;

-- DROP FUNCTION public.gbt_numeric_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_penalty$function$
;

-- DROP FUNCTION public.gbt_numeric_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_picksplit$function$
;

-- DROP FUNCTION public.gbt_numeric_same(gbtreekey_var, gbtreekey_var, internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_same(gbtreekey_var, gbtreekey_var, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_same$function$
;

-- DROP FUNCTION public.gbt_numeric_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_sortsupport$function$
;

-- DROP FUNCTION public.gbt_numeric_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_numeric_union(internal, internal)
 RETURNS gbtreekey_var
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_numeric_union$function$
;

-- DROP FUNCTION public.gbt_oid_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_compress$function$
;

-- DROP FUNCTION public.gbt_oid_consistent(internal, oid, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_consistent(internal, oid, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_consistent$function$
;

-- DROP FUNCTION public.gbt_oid_distance(internal, oid, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_distance(internal, oid, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_distance$function$
;

-- DROP FUNCTION public.gbt_oid_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_fetch$function$
;

-- DROP FUNCTION public.gbt_oid_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_penalty$function$
;

-- DROP FUNCTION public.gbt_oid_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_picksplit$function$
;

-- DROP FUNCTION public.gbt_oid_same(gbtreekey8, gbtreekey8, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_same(gbtreekey8, gbtreekey8, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_same$function$
;

-- DROP FUNCTION public.gbt_oid_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_sortsupport$function$
;

-- DROP FUNCTION public.gbt_oid_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_oid_union(internal, internal)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_oid_union$function$
;

-- DROP FUNCTION public.gbt_text_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_text_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_compress$function$
;

-- DROP FUNCTION public.gbt_text_consistent(internal, text, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_text_consistent(internal, text, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_consistent$function$
;

-- DROP FUNCTION public.gbt_text_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_text_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_penalty$function$
;

-- DROP FUNCTION public.gbt_text_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_text_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_picksplit$function$
;

-- DROP FUNCTION public.gbt_text_same(gbtreekey_var, gbtreekey_var, internal);

CREATE OR REPLACE FUNCTION public.gbt_text_same(gbtreekey_var, gbtreekey_var, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_same$function$
;

-- DROP FUNCTION public.gbt_text_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_text_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_sortsupport$function$
;

-- DROP FUNCTION public.gbt_text_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_text_union(internal, internal)
 RETURNS gbtreekey_var
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_text_union$function$
;

-- DROP FUNCTION public.gbt_time_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_time_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_compress$function$
;

-- DROP FUNCTION public.gbt_time_consistent(internal, time, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_consistent(internal, time without time zone, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_consistent$function$
;

-- DROP FUNCTION public.gbt_time_distance(internal, time, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_distance(internal, time without time zone, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_distance$function$
;

-- DROP FUNCTION public.gbt_time_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_time_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_fetch$function$
;

-- DROP FUNCTION public.gbt_time_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_penalty$function$
;

-- DROP FUNCTION public.gbt_time_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_picksplit$function$
;

-- DROP FUNCTION public.gbt_time_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_same$function$
;

-- DROP FUNCTION public.gbt_time_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_time_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_sortsupport$function$
;

-- DROP FUNCTION public.gbt_time_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_time_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_time_union$function$
;

-- DROP FUNCTION public.gbt_timetz_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_timetz_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_timetz_compress$function$
;

-- DROP FUNCTION public.gbt_timetz_consistent(internal, timetz, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_timetz_consistent(internal, time with time zone, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_timetz_consistent$function$
;

-- DROP FUNCTION public.gbt_ts_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_compress$function$
;

-- DROP FUNCTION public.gbt_ts_consistent(internal, timestamp, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_consistent(internal, timestamp without time zone, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_consistent$function$
;

-- DROP FUNCTION public.gbt_ts_distance(internal, timestamp, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_distance(internal, timestamp without time zone, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_distance$function$
;

-- DROP FUNCTION public.gbt_ts_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_fetch$function$
;

-- DROP FUNCTION public.gbt_ts_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_penalty$function$
;

-- DROP FUNCTION public.gbt_ts_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_picksplit$function$
;

-- DROP FUNCTION public.gbt_ts_same(gbtreekey16, gbtreekey16, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_same(gbtreekey16, gbtreekey16, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_same$function$
;

-- DROP FUNCTION public.gbt_ts_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_sortsupport$function$
;

-- DROP FUNCTION public.gbt_ts_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_ts_union(internal, internal)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_ts_union$function$
;

-- DROP FUNCTION public.gbt_tstz_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_tstz_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_tstz_compress$function$
;

-- DROP FUNCTION public.gbt_tstz_consistent(internal, timestamptz, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_tstz_consistent(internal, timestamp with time zone, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_tstz_consistent$function$
;

-- DROP FUNCTION public.gbt_tstz_distance(internal, timestamptz, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_tstz_distance(internal, timestamp with time zone, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_tstz_distance$function$
;

-- DROP FUNCTION public.gbt_uuid_compress(internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_compress$function$
;

-- DROP FUNCTION public.gbt_uuid_consistent(internal, uuid, int2, oid, internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_consistent(internal, uuid, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_consistent$function$
;

-- DROP FUNCTION public.gbt_uuid_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_fetch$function$
;

-- DROP FUNCTION public.gbt_uuid_penalty(internal, internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_penalty$function$
;

-- DROP FUNCTION public.gbt_uuid_picksplit(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_picksplit$function$
;

-- DROP FUNCTION public.gbt_uuid_same(gbtreekey32, gbtreekey32, internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_same(gbtreekey32, gbtreekey32, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_same$function$
;

-- DROP FUNCTION public.gbt_uuid_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_sortsupport$function$
;

-- DROP FUNCTION public.gbt_uuid_union(internal, internal);

CREATE OR REPLACE FUNCTION public.gbt_uuid_union(internal, internal)
 RETURNS gbtreekey32
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_uuid_union$function$
;

-- DROP FUNCTION public.gbt_var_decompress(internal);

CREATE OR REPLACE FUNCTION public.gbt_var_decompress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_var_decompress$function$
;

-- DROP FUNCTION public.gbt_var_fetch(internal);

CREATE OR REPLACE FUNCTION public.gbt_var_fetch(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_var_fetch$function$
;

-- DROP FUNCTION public.gbt_varbit_sortsupport(internal);

CREATE OR REPLACE FUNCTION public.gbt_varbit_sortsupport(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbt_varbit_sortsupport$function$
;

-- DROP FUNCTION public.gbtreekey16_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey16_in(cstring)
 RETURNS gbtreekey16
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey16_out(gbtreekey16);

CREATE OR REPLACE FUNCTION public.gbtreekey16_out(gbtreekey16)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gbtreekey2_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey2_in(cstring)
 RETURNS gbtreekey2
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey2_out(gbtreekey2);

CREATE OR REPLACE FUNCTION public.gbtreekey2_out(gbtreekey2)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gbtreekey32_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey32_in(cstring)
 RETURNS gbtreekey32
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey32_out(gbtreekey32);

CREATE OR REPLACE FUNCTION public.gbtreekey32_out(gbtreekey32)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gbtreekey4_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey4_in(cstring)
 RETURNS gbtreekey4
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey4_out(gbtreekey4);

CREATE OR REPLACE FUNCTION public.gbtreekey4_out(gbtreekey4)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gbtreekey8_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey8_in(cstring)
 RETURNS gbtreekey8
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey8_out(gbtreekey8);

CREATE OR REPLACE FUNCTION public.gbtreekey8_out(gbtreekey8)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gbtreekey_var_in(cstring);

CREATE OR REPLACE FUNCTION public.gbtreekey_var_in(cstring)
 RETURNS gbtreekey_var
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_in$function$
;

-- DROP FUNCTION public.gbtreekey_var_out(gbtreekey_var);

CREATE OR REPLACE FUNCTION public.gbtreekey_var_out(gbtreekey_var)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gbtreekey_out$function$
;

-- DROP FUNCTION public.gist_translate_cmptype_btree(int4);

CREATE OR REPLACE FUNCTION public.gist_translate_cmptype_btree(integer)
 RETURNS smallint
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$gist_translate_cmptype_btree$function$
;

-- DROP FUNCTION public.increment_lead_search_prompt_version();

CREATE OR REPLACE FUNCTION public.increment_lead_search_prompt_version()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
  BEGIN
    IF NEW.lead_search_prompt IS DISTINCT FROM OLD.lead_search_prompt THEN
      NEW.lead_search_prompt_version = OLD.lead_search_prompt_version + 1;
    END IF;
    RETURN NEW;
  END;
  $function$
;

-- DROP FUNCTION public.int2_dist(int2, int2);

CREATE OR REPLACE FUNCTION public.int2_dist(smallint, smallint)
 RETURNS smallint
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$int2_dist$function$
;

-- DROP FUNCTION public.int4_dist(int4, int4);

CREATE OR REPLACE FUNCTION public.int4_dist(integer, integer)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$int4_dist$function$
;

-- DROP FUNCTION public.int8_dist(int8, int8);

CREATE OR REPLACE FUNCTION public.int8_dist(bigint, bigint)
 RETURNS bigint
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$int8_dist$function$
;

-- DROP FUNCTION public.interval_dist(interval, interval);

CREATE OR REPLACE FUNCTION public.interval_dist(interval, interval)
 RETURNS interval
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$interval_dist$function$
;

-- DROP FUNCTION public.oid_dist(oid, oid);

CREATE OR REPLACE FUNCTION public.oid_dist(oid, oid)
 RETURNS oid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$oid_dist$function$
;

-- DROP FUNCTION public.recount_linked_projects_count();

CREATE OR REPLACE FUNCTION public.recount_linked_projects_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE public.source_channels
           SET linked_projects_count = (
                   SELECT count(*)
                   FROM public.project_source_channels psc
                   WHERE psc.source_channel_id = NEW.source_channel_id
               ),
               updated_at = now()
         WHERE id = NEW.source_channel_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE public.source_channels
           SET linked_projects_count = (
                   SELECT count(*)
                   FROM public.project_source_channels psc
                   WHERE psc.source_channel_id = OLD.source_channel_id
               ),
               updated_at = now()
         WHERE id = OLD.source_channel_id;
        RETURN OLD;
    ELSIF TG_OP = 'UPDATE' THEN
        IF NEW.source_channel_id IS DISTINCT FROM OLD.source_channel_id THEN
            UPDATE public.source_channels
               SET linked_projects_count = (
                       SELECT count(*)
                       FROM public.project_source_channels psc
                       WHERE psc.source_channel_id = OLD.source_channel_id
                   ),
                   updated_at = now()
             WHERE id = OLD.source_channel_id;

            UPDATE public.source_channels
               SET linked_projects_count = (
                       SELECT count(*)
                       FROM public.project_source_channels psc
                       WHERE psc.source_channel_id = NEW.source_channel_id
                   ),
                   updated_at = now()
             WHERE id = NEW.source_channel_id;
        ELSE
            UPDATE public.source_channels
               SET linked_projects_count = (
                       SELECT count(*)
                       FROM public.project_source_channels psc
                       WHERE psc.source_channel_id = NEW.source_channel_id
                   ),
                   updated_at = now()
             WHERE id = NEW.source_channel_id;
        END IF;
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$function$
;

-- DROP FUNCTION public.repoint_is_latest();

CREATE OR REPLACE FUNCTION public.repoint_is_latest()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.is_latest THEN
        UPDATE public.message_ai_screening_runs
           SET is_latest = false,
               updated_at = now()
         WHERE monitoring_project_id = NEW.monitoring_project_id
           AND source_message_id = NEW.source_message_id
           AND is_latest = true
           AND (TG_OP <> 'UPDATE' OR id <> NEW.id);
    END IF;

    RETURN NEW;
END;
$function$
;

-- DROP FUNCTION public.sync_source_channel_active_state();

CREATE OR REPLACE FUNCTION public.sync_source_channel_active_state()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
    DECLARE
      affected_channel_id BIGINT;
      enabled_count INT;
    BEGIN
      -- Определяем затронутый канал
      IF TG_OP = 'DELETE' THEN
        affected_channel_id := OLD.source_channel_id;
      ELSE
        affected_channel_id := NEW.source_channel_id;
      END IF;

      -- Считаем сколько проектов используют этот канал и включили его
      SELECT COUNT(*) INTO enabled_count
      FROM project_source_channels
      WHERE source_channel_id = affected_channel_id
        AND is_enabled = true;

      -- Обновляем канал: is_active и linked_projects_count
      UPDATE source_channels
      SET
        is_active            = (enabled_count > 0),
        linked_projects_count = enabled_count,
        updated_at           = NOW()
      WHERE id = affected_channel_id;

      RETURN NULL;
    END;
    $function$
;

-- DROP FUNCTION public.time_dist(time, time);

CREATE OR REPLACE FUNCTION public.time_dist(time without time zone, time without time zone)
 RETURNS interval
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$time_dist$function$
;

-- DROP FUNCTION public.ts_dist(timestamp, timestamp);

CREATE OR REPLACE FUNCTION public.ts_dist(timestamp without time zone, timestamp without time zone)
 RETURNS interval
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$ts_dist$function$
;

-- DROP FUNCTION public.tstz_dist(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.tstz_dist(timestamp with time zone, timestamp with time zone)
 RETURNS interval
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/btree_gist', $function$tstz_dist$function$
;

-- DROP FUNCTION public.update_updated_at();

CREATE OR REPLACE FUNCTION public.update_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$function$
;