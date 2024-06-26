import json
import time
from unittest import mock

from django.contrib.auth.models import User
from django.db.models import Q
from django.test import Client, TestCase, TransactionTestCase

from meshapi.models import Building, Install, Member, Node
from meshapi.views import JoinFormRequest

from .sample_data import sample_building, sample_node
from .sample_join_form_data import (
    bronx_join_form_submission,
    jefferson_join_form_submission,
    kings_join_form_submission,
    non_nyc_join_form_submission,
    queens_join_form_submission,
    richmond_join_form_submission,
    valid_join_form_submission,
)
from .util import TestThread

# Grab a reference to the original Install.__init__ function, so that when it gets mocked
# we can still use it when we need it
original_install_init = Install.__init__


def validate_successful_join_form_submission(test_case, test_name, s, response):
    # Make sure that we get the right stuff out of the database afterwards

    # Check if the member was created and that we see it when we
    # filter for it.
    existing_members = Member.objects.filter(
        Q(
            name=s.first_name + " " + s.last_name,
            phone_number=s.phone,
        )
        & (
            Q(primary_email_address=s.email)
            | Q(stripe_email_address=s.email)
            | Q(additional_email_addresses__contains=[s.email])
        )
    )

    length = 1
    test_case.assertEqual(
        len(existing_members),
        length,
        f"Didn't find created member for {test_name}. Should be {length}, but got {len(existing_members)}",
    )

    # Check if the building was created and that we see it when we
    # filter for it.
    existing_buildings = Building.objects.filter(
        street_address=s.street_address,
        city=s.city,
        state=s.state,
        zip_code=s.zip,
    )

    length = 1
    test_case.assertEqual(
        len(existing_buildings),
        length,
        f"Didn't find created building for {test_name}. Should be {length}, but got {len(existing_buildings)}",
    )

    # Check that a install was created
    install_number = json.loads(response.content.decode("utf-8"))["install_number"]
    join_form_installs = Install.objects.filter(pk=install_number)

    length = 1
    test_case.assertEqual(
        len(join_form_installs),
        length,
        f"Didn't find created install for {test_name}. Should be {length}, but got {len(join_form_installs)}",
    )


# Pulls the parsed_street_address out of the test data so that we don't have to later
# Returns JSON and a JoinFormRequest in the correct format to be given to the above function
def pull_apart_join_form_submission(submission):
    request = submission.copy()
    del request["parsed_street_address"]

    # Make sure that we get the right stuff out of the database afterwards
    s = JoinFormRequest(**request)

    # Match the format from OSM. I did this to see how OSM would mutate the
    # raw request we get.
    s.street_address = submission["parsed_street_address"]
    s.city = submission["city"]
    s.state = submission["state"]

    return request, s


class TestJoinForm(TestCase):
    c = Client()
    admin_c = Client()

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin", password="admin_password", email="admin@example.com"
        )
        self.admin_c.login(username="admin", password="admin_password")

    def test_valid_join_form(self):
        for submission in [
            valid_join_form_submission,
            richmond_join_form_submission,
            kings_join_form_submission,
            queens_join_form_submission,
            bronx_join_form_submission,
        ]:
            request, s = pull_apart_join_form_submission(submission)

            response = self.c.post("/api/v1/join/", request, content_type="application/json")
            code = 201
            self.assertEqual(
                code,
                response.status_code,
                f"status code incorrect for Valid Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
            )
            validate_successful_join_form_submission(self, "Valid Join Form", s, response)

    def test_no_ncl(self):
        request, _ = pull_apart_join_form_submission(valid_join_form_submission)

        request["ncl"] = False

        response = self.c.post("/api/v1/join/", request, content_type="application/json")
        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for No NCL. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )

    def test_non_nyc_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, _ = pull_apart_join_form_submission(non_nyc_join_form_submission)
        response = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Non NYC Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )

    def test_empty_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        response = self.c.post("/api/v1/join/", {}, content_type="application/json")

        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Empty Join Form. Should be {code}, but got {response.status_code}",
        )

        # Shouldn't have any data in the database
        existing_members = Member.objects.all()
        length = 0
        self.assertEqual(
            len(existing_members),
            length,
            f"Didn't find created member for Empty Join Form. Should be {length}, but got {len(existing_members)}",
        )

        existing_buildings = Building.objects.all()

        length = 0
        self.assertEqual(
            len(existing_buildings),
            length,
            f"Search for created building for Empty Join Form was wrong. Should be {length}, but got {len(existing_buildings)}",
        )

    def test_bad_phone_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, _ = pull_apart_join_form_submission(valid_join_form_submission)
        form["phone"] = "555-555-5555"
        response = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Bad Phone Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )

        con = json.loads(response.content.decode("utf-8"))

        self.assertEqual("555-555-5555 is not a valid phone number", con["detail"], "Content is wrong")

    def test_bad_email_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, _ = pull_apart_join_form_submission(valid_join_form_submission)
        form["email"] = "notareal@email.meshmeshmeshmeshmesh"
        response = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Bad Email Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )

        con = json.loads(response.content.decode("utf-8"))

        self.assertEqual(
            "notareal@email.meshmeshmeshmeshmesh is not a valid email",
            con["detail"],
            "Content is wrong",
        )

    def test_bad_address_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, _ = pull_apart_join_form_submission(valid_join_form_submission)
        form["street_address"] = "fjdfahuweildhjweiklfhjkhklfhj"
        response = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 400
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Bad Address Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )

        con = json.loads(response.content.decode("utf-8"))

        self.assertEqual(
            f"(NYC) Address '{form['street_address']}, {form['city']}, {form['state']} {form['zip']}' not found in geosearch.planninglabs.nyc.",
            con["detail"],
            f"Did not get correct response content for bad address join form: {response.content.decode('utf-8')}",
        )

    def test_member_moved_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, s = pull_apart_join_form_submission(valid_join_form_submission)
        response1 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response1.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response1.status_code}.\n Response is: {response1.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "Valid Join Form", s, response1)

        # Now test that the member can "move" and still access the join form
        v_sub_2 = valid_join_form_submission.copy()
        v_sub_2["street_address"] = "152 Broome Street"

        form, s = pull_apart_join_form_submission(v_sub_2)

        # Name, email, phone, location, apt, rooftop, referral
        response2 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response2.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response2.status_code}.\n Response is: {response2.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "Valid Join Form", s, response2)

        self.assertEqual(
            json.loads(
                response1.content.decode("utf-8"),
            )["member_id"],
            json.loads(
                response2.content.decode("utf-8"),
            )["member_id"],
        )

    def test_different_street_addr_same_bin_multi_node(self):
        """ "
        This test case simulates a new building joining the Jefferson structure to
        make sure we handle the multi-address multi-node structures correctly
        """

        # Name, email, phone, location, apt, rooftop, referral
        form, s = pull_apart_join_form_submission(jefferson_join_form_submission)
        response1 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response1.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response1.status_code}.\n Response is: {response1.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "Valid Join Form", s, response1)

        # Now test that the member can "move" and still access the join form
        v_sub_2 = jefferson_join_form_submission.copy()
        v_sub_2["street_address"] = "16 Cypress Avenue"
        v_sub_2["apartment"] = "13"

        building_id_1 = json.loads(
            response1.content.decode("utf-8"),
        )["building_id"]
        building1 = Building.objects.get(id=building_id_1)
        building1.primary_node = Node(
            status=Node.NodeStatus.ACTIVE,
            latitude=building1.latitude,
            longitude=building1.longitude,
            altitude=building1.altitude,
        )
        building1.primary_node.save()
        building1.save()

        additional_node_1_for_building = Node(
            status=Node.NodeStatus.ACTIVE,
            latitude=building1.latitude,
            longitude=building1.longitude,
            altitude=building1.altitude,
        )
        additional_node_1_for_building.save()
        building1.nodes.add(additional_node_1_for_building)

        additional_node_2_for_building = Node(
            status=Node.NodeStatus.INACTIVE,
            latitude=building1.latitude,
            longitude=building1.longitude,
            altitude=building1.altitude,
        )
        additional_node_2_for_building.save()
        building1.nodes.add(additional_node_2_for_building)

        form, s = pull_apart_join_form_submission(v_sub_2)

        # Name, email, phone, location, apt, rooftop, referral
        response2 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response2.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response2.status_code}.\n Response is: {response2.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "Valid Join Form", s, response2)

        building_id_2 = json.loads(
            response2.content.decode("utf-8"),
        )["building_id"]

        self.assertNotEqual(building_id_1, building_id_2)

        building2 = Building.objects.get(id=building_id_2)
        self.assertEqual(building1.primary_node, building2.primary_node)
        self.assertEqual(
            set(building1.nodes.all().values_list("network_number", flat=True)),
            set(building2.nodes.all().values_list("network_number", flat=True)),
        )

    def test_member_moved_and_used_stripe_email_join_form(self):
        # Name, email, phone, location, apt, rooftop, referral
        form, s = pull_apart_join_form_submission(valid_join_form_submission)
        response1 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response1.status_code,
            f"status code incorrect. Should be {code}, but got {response1.status_code}.\n Response is: {response1.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "", s, response1)

        member_object = Member.objects.get(id=json.loads(response1.content.decode("utf-8"))["member_id"])
        member_object.stripe_email_address = "jsmith+stripe@gmail.com"
        member_object.additional_email_addresses = ["jsmith+other@gmail.com"]
        member_object.save()

        # Now test that the member can "move", use the stripe email address,
        # and we will connect it to their old registration
        v_sub_2 = valid_join_form_submission.copy()
        v_sub_2["email"] = "jsmith+stripe@gmail.com"
        v_sub_2["street_address"] = "152 Broome Street"

        form, s = pull_apart_join_form_submission(v_sub_2)

        # Name, email, phone, location, apt, rooftop, referral
        response2 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response2.status_code,
            f"status code incorrect. Should be {code}, "
            f"but got {response2.status_code}.\n Response is: {response2.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "", s, response2)

        self.assertEqual(
            member_object.id,
            json.loads(
                response2.content.decode("utf-8"),
            )["member_id"],
        )

        # Now test that the member can "move" again, use an additional email address,
        # and we will connect it to their old registration
        v_sub_3 = valid_join_form_submission.copy()
        v_sub_3["email"] = "jsmith+other@gmail.com"
        v_sub_3["street_address"] = "178 Broome Street"

        form, s = pull_apart_join_form_submission(v_sub_3)

        # Name, email, phone, location, apt, rooftop, referral
        response3 = self.c.post("/api/v1/join/", form, content_type="application/json")

        code = 201
        self.assertEqual(
            code,
            response3.status_code,
            f"status code incorrect. Should be {code}, "
            f"but got {response3.status_code}.\n Response is: {response3.content.decode('utf-8')}",
        )

        validate_successful_join_form_submission(self, "Valid Join Form", s, response3)

        self.assertEqual(
            member_object.id,
            json.loads(
                response3.content.decode("utf-8"),
            )["member_id"],
        )

    def test_pre_existing_building_and_node(self):
        request, s = pull_apart_join_form_submission(valid_join_form_submission)

        node = Node(**sample_node)
        node.save()

        building = Building(
            street_address="151 Broome Street",
            city="New York",
            state="NY",
            zip_code="10002",
            bin=1077609,
            latitude=0.0,
            longitude=0.0,
            altitude=0.0,
            address_truth_sources=["NYCPlanningLabs"],
            primary_node=node,
        )
        building.save()

        response = self.c.post("/api/v1/join/", request, content_type="application/json")
        code = 201
        self.assertEqual(
            code,
            response.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response.status_code}.\n Response is: {response.content.decode('utf-8')}",
        )
        validate_successful_join_form_submission(self, "Valid Join Form", s, response)

        install_number = json.loads(response.content.decode("utf-8"))["install_number"]
        install = Install.objects.get(install_number=install_number)

        self.assertEqual(install.building.id, building.id)
        self.assertEqual(install.node.network_number, node.network_number)


def slow_install_init(*args, **kwargs):
    result = original_install_init(*args, **kwargs)
    time.sleep(2)
    return result


class TestJoinFormRaceCondition(TransactionTestCase):
    c = Client()
    admin_c = Client()

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin", password="admin_password", email="admin@example.com"
        )
        self.admin_c.login(username="admin", password="admin_password")

        # This isn't used by the test cases here, but because of the slightly hacky way
        # we lock the DB, there needs to be at least one Member or Building object in order
        # for locking to work correctly
        building = Building(**sample_building)
        building.save()

    def test_valid_join_form(self):
        results = []

        member1_submission = valid_join_form_submission.copy()
        member1_submission["email"] = "member1@xyz.com"
        member2_submission = valid_join_form_submission.copy()
        member2_submission["email"] = "member2@xyz.com"

        def invoke_join_form(submission, results):
            # Slow down the creation of the Install object to force a race condition
            with mock.patch("meshapi.views.forms.Install.__init__", slow_install_init):
                request, s = pull_apart_join_form_submission(submission)
                response = self.c.post("/api/v1/join/", request, content_type="application/json")
                results.append(response)

        t1 = TestThread(target=invoke_join_form, args=(member1_submission, results))
        time.sleep(0.5)  # Sleep to give the first thread a head start
        t2 = TestThread(target=invoke_join_form, args=(member2_submission, results))

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        response1 = results[0]
        response2 = results[1]

        code = 201
        self.assertEqual(
            code,
            response1.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response1.status_code}.\n Response is: {response1.content.decode('utf-8')}",
        )

        code = 201
        self.assertEqual(
            code,
            response2.status_code,
            f"status code incorrect for Valid Join Form. Should be {code}, but got {response2.status_code}.\n Response is: {response2.content.decode('utf-8')}",
        )

        # Make sure that duplicate buildings were not created
        assert response1.data["building_id"] == response2.data["building_id"]
        assert response1.data["member_id"] != response2.data["member_id"]
        assert response1.data["install_number"] != response2.data["install_number"]
