CREATE TABLE public.account (
    id integer NOT NULL,
    display_name character varying,
    location character varying,
    about_me character varying,
    website_url character varying
);

CREATE TABLE public.answer (
    id integer NOT NULL,
    site_id integer NOT NULL,
    question_id integer,
    creation_date timestamp without time zone,
    deletion_date timestamp without time zone,
    score integer,
    view_count integer,
    body character varying,
    owner_user_id integer,
    last_editor_id integer,
    last_edit_date timestamp without time zone,
    last_activity_date timestamp without time zone,
    title character varying
);

CREATE TABLE public.badge (
    site_id integer NOT NULL,
    user_id integer NOT NULL,
    name character varying NOT NULL,
    date timestamp without time zone NOT NULL
);

CREATE TABLE public.comment (
    id integer NOT NULL,
    site_id integer NOT NULL,
    post_id integer,
    user_id integer,
    score integer,
    body character varying,
    date timestamp without time zone
);

CREATE TABLE public.post_link (
    site_id integer NOT NULL,
    post_id_from integer NOT NULL,
    post_id_to integer NOT NULL,
    link_type integer NOT NULL,
    date timestamp without time zone
);

CREATE TABLE public.question (
    id integer NOT NULL,
    site_id integer NOT NULL,
    accepted_answer_id integer,
    creation_date timestamp without time zone,
    deletion_date timestamp without time zone,
    score integer,
    view_count integer,
    body character varying,
    owner_user_id integer,
    last_editor_id integer,
    last_edit_date timestamp without time zone,
    last_activity_date timestamp without time zone,
    title character varying,
    favorite_count integer,
    closed_date timestamp without time zone,
    tagstring character varying
);
CREATE TABLE public.site (
    site_id integer NOT NULL,
    site_name character varying
);
CREATE TABLE public.so_user (
    id integer NOT NULL,
    site_id integer NOT NULL,
    reputation integer,
    creation_date timestamp without time zone,
    last_access_date timestamp without time zone,
    upvotes integer,
    downvotes integer,
    account_id integer
);

CREATE TABLE public.tag (
    id integer NOT NULL,
    site_id integer NOT NULL,
    name character varying
);

CREATE TABLE public.tag_question (
    question_id integer NOT NULL,
    tag_id integer NOT NULL,
    site_id integer NOT NULL
);

ALTER TABLE ONLY public.account
    ADD CONSTRAINT account_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.answer
    ADD CONSTRAINT answer_pkey PRIMARY KEY (id, site_id);

ALTER TABLE ONLY public.badge
    ADD CONSTRAINT badge_pkey PRIMARY KEY (site_id, user_id, name, date);

ALTER TABLE ONLY public.comment
    ADD CONSTRAINT comment_pkey PRIMARY KEY (id, site_id);

ALTER TABLE ONLY public.post_link
    ADD CONSTRAINT post_link_pkey PRIMARY KEY (site_id, post_id_from, post_id_to, link_type);

ALTER TABLE ONLY public.question
    ADD CONSTRAINT question_pkey PRIMARY KEY (id, site_id);

ALTER TABLE ONLY public.site
    ADD CONSTRAINT site_pkey PRIMARY KEY (site_id);

ALTER TABLE ONLY public.so_user
    ADD CONSTRAINT so_user_pkey PRIMARY KEY (id, site_id);
ALTER TABLE ONLY public.tag
    ADD CONSTRAINT tag_pkey PRIMARY KEY (id, site_id);

ALTER TABLE ONLY public.tag_question
    ADD CONSTRAINT tag_question_pkey PRIMARY KEY (site_id, question_id, tag_id);

ALTER TABLE ONLY public.answer
    ADD CONSTRAINT answer_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.answer
    ADD CONSTRAINT answer_site_id_fkey1 FOREIGN KEY (site_id, owner_user_id) REFERENCES public.so_user(site_id, id);

ALTER TABLE ONLY public.answer
    ADD CONSTRAINT answer_site_id_fkey2 FOREIGN KEY (site_id, last_editor_id) REFERENCES public.so_user(site_id, id);

ALTER TABLE ONLY public.answer
    ADD CONSTRAINT answer_site_id_fkey3 FOREIGN KEY (site_id, question_id) REFERENCES public.question(site_id, id);

ALTER TABLE ONLY public.badge
    ADD CONSTRAINT badge_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.badge
    ADD CONSTRAINT badge_site_id_fkey1 FOREIGN KEY (site_id, user_id) REFERENCES public.so_user(site_id, id);

ALTER TABLE ONLY public.comment
    ADD CONSTRAINT comment_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.post_link
    ADD CONSTRAINT post_link_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.post_link
    ADD CONSTRAINT post_link_site_id_fkey1 FOREIGN KEY (site_id, post_id_to) REFERENCES public.question(site_id, id);

ALTER TABLE ONLY public.post_link
    ADD CONSTRAINT post_link_site_id_fkey2 FOREIGN KEY (site_id, post_id_from) REFERENCES public.question(site_id, id);

ALTER TABLE ONLY public.question
    ADD CONSTRAINT question_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.question
    ADD CONSTRAINT question_site_id_fkey1 FOREIGN KEY (site_id, owner_user_id) REFERENCES public.so_user(site_id, id);

ALTER TABLE ONLY public.question
    ADD CONSTRAINT question_site_id_fkey2 FOREIGN KEY (site_id, last_editor_id) REFERENCES public.so_user(site_id, id);

ALTER TABLE ONLY public.so_user
    ADD CONSTRAINT so_user_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.account(id);

ALTER TABLE ONLY public.so_user
    ADD CONSTRAINT so_user_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.tag_question
    ADD CONSTRAINT tag_question_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);

ALTER TABLE ONLY public.tag_question
    ADD CONSTRAINT tag_question_site_id_fkey1 FOREIGN KEY (site_id, tag_id) REFERENCES public.tag(site_id, id);

ALTER TABLE ONLY public.tag_question
    ADD CONSTRAINT tag_question_site_id_fkey2 FOREIGN KEY (site_id, question_id) REFERENCES public.question(site_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tag
    ADD CONSTRAINT tag_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.site(site_id);
